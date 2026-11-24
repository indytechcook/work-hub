"""Outbound-only source collectors. Checkpoints advance after durable receipt writes."""
import fcntl
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
from defusedxml import ElementTree as ET
from pathlib import Path
import subprocess
import sys
import time
from store import Store, digest
from settings import selected_sources,slack as slack_settings,betterstack as error_settings
ROOT=Path(__file__).resolve().parent
STATE=ROOT/'.state'

def failure_detail(error):
    """Keep exception categories, never remote payloads, OAuth URLs, or tokens."""
    def leaves(exc):
        children=getattr(exc,'exceptions',())
        return [name for child in children for name in leaves(child)] if children else [type(exc).__name__]
    names=sorted(set(leaves(error)))
    if 'SourceSignInRequired' in names:detail='Source sign-in needs renewal'
    elif 'SourceAuthUnavailable' in names:detail='Source authorization service unavailable; will retry'
    else:detail='Collection failed ('+', '.join(names[:5])+')'
    return detail+'; previous checkpoint retained'


def run(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=90)
    if result.returncode:raise RuntimeError('Source command failed')
    return result.stdout


def timestamp(value):
    return dt.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()


def receipt(external_id,title,body,**extra):
    encoded=body if isinstance(body,str) else json.dumps(body,ensure_ascii=False)
    if len(encoded)>59000:encoded=encoded[:58500]+'\n[COVERAGE: source excerpt truncated; full conversation not captured]'
    return dict(external_id=external_id,title=title[:1000],text=encoded,version=digest(title,encoded),**extra)


class Collector:
    def __init__(self,store):self.store=store
    def checkpoint(self,name):
        row=self.store.db.execute('SELECT checkpoint FROM sources WHERE name=?',(name,)).fetchone()
        return json.loads(row[0]) if row and row[0] else None
    def save(self,name,items,scope,checkpoint=None,complete=True):
        for offset in range(0,len(items),500):self.store.ingest(name,items[offset:offset+500],scope=scope,complete=False)
        return self.store.ingest(name,[],scope=scope,checkpoint=checkpoint,complete=complete)
    def google(self):
        adapter=ROOT.parent/'connectors/google'
        if str(adapter) not in sys.path:sys.path.insert(0,str(adapter))
        import server
        original=server.google_get
        if not getattr(original,'hub_retry',False):
            def retried(url,params):
                for attempt in range(5):
                    try:return original(url,params)
                    except Exception as error:
                        # The adapter emits sanitized status text, never response bodies.
                        if attempt==4 or not any(code in str(error) for code in ['HTTP 403','HTTP 429','HTTP 500','HTTP 502','HTTP 503']):raise
                        time.sleep(2**attempt)
            retried.hub_retry=True
            server.google_get=retried
        return server
    def gmail(self):
        g=self.google();now=time.time();cp=self.checkpoint('gmail')
        after=(cp['through']-86400) if cp else now-7*86400
        scope='Gmail inbox and sent mail since initial 7-day capture; one-day overlap on each scan; plain text only, no attachments'
        token='';ids=set();message_ids={};complete=True
        for _ in range(100):
            page=g.google_gmail_search(f'after:{int(after)} (in:inbox OR in:sent) -category:promotions -category:social',50,token)
            ids.update(m['threadId'] for m in page['items'])
            for m in page['items']:message_ids.setdefault(m['threadId'],set()).add(m['id'])
            token=page['next_page_token']
            if not token:break
        else:complete=False
        # Re-read conversations with obligations, even if their inbox labels changed.
        ids.update(r[0] for r in self.store.db.execute("SELECT DISTINCT c.external_id FROM conversations c JOIN obligations o ON o.conversation_id=c.id WHERE c.source='gmail' AND o.state IN ('open','needs_review')"))
        items=[]
        for tid in sorted(ids):
            old=self.store.db.execute("SELECT e.text FROM conversations c JOIN events e ON e.id=c.latest_event WHERE c.source='gmail' AND c.external_id=?",(tid,)).fetchone()
            if old:
                try:previous=json.loads(old[0]);known=set(previous.get('message_ids',[])) or {m['id'] for m in previous.get('messages',[])}
                except json.JSONDecodeError:known=set()
                # New Gmail messages are immutable; only unseen IDs require a body read.
                if message_ids.get(tid) and message_ids[tid].issubset(known):continue
            time.sleep(2)
            item=self.gmail_thread(g,tid)
            if item:items.append(item)
            if len(items)>=20:
                self.store.ingest('gmail',items,scope=scope,complete=False);items=[]
        return self.save('gmail',items,scope,{'through':now},complete)
    def gmail_thread(self,g,tid):
        raw=g.google_get('https://gmail.googleapis.com/gmail/v1/users/me/threads/'+tid,{'format':'full'})
        raw_messages=raw.get('messages',[]);messages=[]
        for m in raw_messages[-50:]:
            payload=m.get('payload',{});body='\n'.join(g.plain_parts(payload))
            messages.append({'id':m['id'],'internal_date':m.get('internalDate'),'headers':[h for h in payload.get('headers',[]) if h['name'].lower() in {'from','to','cc','subject','date'}],'text':body[:12000],'text_truncated':len(body)>12000,'plain_text_available':bool(body),'snippet':m.get('snippet','')})
        incomplete=len(raw_messages)>50 or any(m['text_truncated'] or not m['plain_text_available'] for m in messages)
        if not messages:return None
        # Preserve the newest evidence within the receipt limit, never cut JSON in half.
        bounded=[];size=0
        for message in reversed(messages):
            cost=len(json.dumps(message,ensure_ascii=False))
            if size+cost>44000:incomplete=True;break
            bounded.insert(0,message);size+=cost
        headers={h['name'].lower():h['value'] for h in messages[-1]['headers']}
        return receipt(tid,headers.get('subject','Email conversation'),dict(messages=bounded,message_ids=[m['id'] for m in raw_messages[-500:]],coverage='Partial plain-text excerpt' if incomplete else 'Latest 50 plain-text messages; attachments excluded'),occurred_at=max(float(m['internal_date'])/1000 for m in messages),url=f'https://mail.google.com/mail/u/0/#all/{tid}')
    def calendar(self):
        g=self.google();now=dt.datetime.now(dt.timezone.utc);token='';items=[];complete=True
        for _ in range(50):
            page=g.google_calendar_events(now-dt.timedelta(days=7),now+dt.timedelta(days=30),'primary',50,token)
            for e in page['items']:
                items.append(receipt(e['id'],e.get('summary','Cancelled calendar entry'),e,occurred_at=timestamp(e['updated']) if e.get('updated') else time.time(),url=e.get('htmlLink','')))
            token=page['next_page_token']
            if not token:break
        else:complete=False
        return self.save('calendar',items,'Primary calendar: previous 7 days and next 30 days, including cancelled events',{'through':now.isoformat()},complete)
    def drive(self):
        g=self.google();cutoff=dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=7);token='';items=[];complete=True
        for _ in range(50):
            page=g.google_drive_search("trashed = false and modifiedTime > '"+cutoff.isoformat()+"'",50,token)
            for f in page['items']:items.append(receipt(f['id'],f['name'],dict(metadata=f,coverage='File metadata only; document content and comments NOT captured'),url=f.get('webViewLink',''),occurred_at=timestamp(f['modifiedTime'])))
            if page['incomplete_search']:complete=False
            token=page['next_page_token']
            if not token:break
        else:complete=False
        return self.save('drive',items,'Drive metadata changed in previous 7 days; no document contents or comments',{'through':time.time()},complete)
    def github(self):
        items=[];complete=True
        for flag in ['--author','--review-requested']:
            rows=json.loads(run(['gh','search','prs',flag,'@me','--state','open','--limit','100','--json','id,number,title,body,updatedAt,url,repository']))
            if len(rows)>=100:complete=False
            for p in rows:items.append(receipt(p['id'],p['title'],p,occurred_at=timestamp(p['updatedAt']),url=p['url']))
        return self.save('github',items,'Open PRs authored by you or requesting your review; descriptions only, no full review/comment history',{'through':time.time()},complete)
    def linear(self):
        run(['bb','linear','sync'])
        rows=[];complete=True
        for state in ['started','unstarted','backlog','triage']:
            page=json.loads(run(['bb','linear','issues','--state',state,'--assignee','me','--limit','200','--json']))['issues']
            rows.extend(page)
            if len(page)>=200:complete=False
        ids={r[0] for r in rows}
        ids.update(r[0] for r in self.store.db.execute("SELECT c.external_id FROM conversations c JOIN obligations o ON o.conversation_id=c.id WHERE c.source='linear' AND o.state IN ('open','needs_review')"))
        items=[]
        for identifier in sorted(ids):
            body=run(['bb','linear','issue',identifier,'--comments']);lines=body.splitlines()
            url=next((l[5:].strip() for l in lines if l.startswith('URL: ')),'')
            items.append(receipt(identifier,lines[0] if lines else identifier,body,url=url,occurred_at=time.time()))
        scope='Open assigned issues in BB-bound teams, known open follow-ups, recent comments and project names for captured issue links'
        self.save('linear',items,scope,complete=False)
        from linear_projects import refresh_projects
        refresh_projects(self.store)
        return self.save('linear',[],scope,{'through':time.time()},complete)
    def slack(self):
        from slack import call
        cp=self.checkpoint('slack');now=time.time();after=(cp['through']-86400) if cp else now-7*86400
        date=dt.datetime.fromtimestamp(after,dt.timezone.utc).date().isoformat()
        scope='Slack direct reads: DMs/group DMs, your channel messages and channel mentions; initial 7 days, then one-day overlap; thread replies, no file contents'
        searches=[{'filters':'after:'+date,'channel_types':'im,mpim'}, {'filters':'after:'+date+' from:me','channel_types':'public_channel,private_channel'}, {'filters':'after:'+date+' to:me','channel_types':'public_channel,private_channel'}]
        seen=set();complete=True
        def unpack(result):
            contents=[c['text'] for c in result.get('content',[]) if c['type']=='text']
            if len(contents)!=1:raise ValueError('Unexpected Slack response')
            return json.loads(contents[0])
        def cursor(page):
            info=page.get('pagination_info','')
            match=re.search(r'cursor `([^`]+)`',info)
            if match:return match.group(1)
            if 'no more' in info.lower() or not info.strip():return None
            raise ValueError('Unrecognized Slack pagination; refusing to mark source complete')
        for query in searches:
            next_cursor=None
            for _ in range(30):
                args=dict(query,limit=20,sort='timestamp',sort_dir='asc',response_format='detailed',include_context=False)
                if next_cursor:args['cursor']=next_cursor
                page=unpack(call('slack_search_public_and_private',args))
                blocks=re.split(r'(?=^Channel:)',page['results'],flags=re.M)
                for block in blocks:
                    channel=re.search(r'^Channel:.*\(ID: ([A-Z0-9]+)\)',block,re.M)
                    stamp=re.search(r'^Message_ts: ([0-9.]+)',block,re.M)
                    if not channel or not stamp:continue
                    key=(channel.group(1),stamp.group(1))
                    if key in seen:continue
                    seen.add(key);messages=[];thread_cursor=None
                    for __ in range(10):
                        time.sleep(1.3)
                        args={'channel_id':key[0],'message_ts':key[1],'limit':100,'response_format':'detailed'}
                        if thread_cursor:args['cursor']=thread_cursor
                        thread=unpack(call('slack_read_thread',args));messages.append(thread['messages']);thread_cursor=cursor(thread)
                        if not thread_cursor:break
                    else:complete=False
                    body='\n'.join(messages)
                    parent=re.search(r'^Message TS: ([0-9.]+)',body,re.M)
                    parent_ts=parent.group(1) if parent else key[1]
                    times=re.findall(r'^Message TS: ([0-9.]+)',body,re.M)
                    link=slack_settings()['workspace_url']+'/archives/'+key[0]+'/p'+parent_ts.replace('.','')
                    title='Slack · '+block.splitlines()[0].removeprefix('Channel: ')
                    item=receipt(key[0]+':'+parent_ts,title,body,occurred_at=max([float(t) for t in times] or [float(key[1])]),url=link)
                    self.store.ingest('slack',[item],scope=scope,complete=False)
                next_cursor=cursor(page)
                if not next_cursor:break
            else:complete=False
        return self.save('slack',[],scope,{'through':now},complete)

    def meetings(self):
        from mcp_access import call
        cp=self.checkpoint('meetings');now=time.time();next_cursor=None;complete=True
        scope='Granola meeting notes and summaries: initial 7 days, then changed notes with one-day overlap; attachments and full transcripts excluded'
        filters={'updated_after':dt.datetime.fromtimestamp(cp['through']-86400,dt.timezone.utc).isoformat()} if cp else {'time_range':'last_7_days'}
        def content(result):return '\n'.join(c['text'] for c in result.get('content',[]) if c['type']=='text')
        for _ in range(50):
            args=dict(filters,limit=100,intent='Track commitments and return points from meeting notes.')
            if next_cursor:args['cursor']=next_cursor
            response=content(call('granola','list_meetings',args))
            if len(response)>2_000_000:raise ValueError('Meeting response exceeds the size limit')
            root=ET.fromstring('<root>'+response+'</root>',forbid_dtd=True,forbid_entities=True,forbid_external=True)
            pagination=root.find('pagination')
            if pagination is None:raise ValueError('Missing meeting pagination')
            for meeting in root.findall('.//meeting'):
                mid=meeting.attrib['id']
                notes=content(call('granola','get_meetings',{'meeting_ids':[mid],'attachments':'none','intent':'Track commitments and next actions from meeting notes.'}))
                # Vendor agent guidance is not meeting evidence.
                notes=re.sub(r'<context_for_agents>.*?</context_for_agents>','',notes,flags=re.S)
                notes=re.sub(r'<attachments_guidance>.*?</attachments_guidance>','',notes,flags=re.S)
                if len(notes)>58000:complete=False
                evidence='SOURCE: Granola notes and AI-assisted summary, not a verified verbatim transcript.\n'+notes
                item=receipt(mid,meeting.attrib.get('title','Meeting notes'),evidence,url=meeting.attrib.get('url',''),occurred_at=timestamp(meeting.attrib['updated_at']) if meeting.attrib.get('updated_at') else now)
                self.store.ingest('meetings',[item],scope=scope,complete=False)
            if pagination.attrib.get('has_more')=='false':break
            next_cursor=pagination.attrib.get('next_cursor')
            if not next_cursor:raise ValueError('Missing meeting continuation cursor')
        else:complete=False
        return self.save('meetings',[],scope,{'through':now},complete)

    def betterstack(self):
        from mcp_access import call
        config=error_settings()
        scope=f"Application {config['application_id']}, {config['environment']} error patterns seen in the last 7 days; observation only"
        seen=set();complete=True
        for page in range(1,101):
            result=call('betterstack','errors',{'application_id':config['application_id'],'state':'all','environment':config['environment'],'start_time':'now-7d','page':page})
            body='\n'.join(c['text'] for c in result.get('content',[]) if c['type']=='text')
            rows=[line for line in body.splitlines() if re.match(r'^[0-9a-f]{64}\s*\|',line)]
            if not rows:
                if not any(word in body.lower() for word in ['no errors','no data','no matching']):raise ValueError('Unrecognized error response; refusing to advance coverage')
                break
            for row in rows:
                fields=[v.strip() for v in row.split('|')]
                pattern=fields[0]
                if pattern in seen:raise ValueError('Error pagination repeated a page')
                seen.add(pattern)
                if len(fields)<9:raise ValueError('Unrecognized error table')
                link=re.search(r'\(https://[^)]+\)',fields[-1])
                url=link.group(0)[1:-1] if link else ''
                item=receipt(pattern,fields[1]+': '+fields[2],dict(application_id=config['application_id'],pattern=pattern,error_type=fields[1],message=fields[2],count=fields[3],users=fields[4],triage_state=fields[5],last_seen=fields[6],coverage='Error group metadata only; no stack trace or root cause diagnosis'),url=url,occurred_at=time.time())
                self.store.ingest('betterstack',[item],scope=scope,complete=False)
        else:complete=False
        return self.save('betterstack',[],scope,{'through':time.time()},complete)

    def collect(self,name):
        lock=(STATE/(name+'.collector.lock')).open('a')
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:lock.close();return {'state':'already_running'}
        try:return getattr(self,name)()
        except Exception as error:
            detail=failure_detail(error)
            self.store.source_failure(name,detail)
            return {'state':'error','error_type':type(error).__name__,'detail':detail}
        finally:lock.close()

if __name__=='__main__':
    os.umask(0o077);STATE.mkdir(mode=0o700,exist_ok=True)
    lock=(STATE/'collector.lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:print(json.dumps({'state':'already_running'}));raise SystemExit(0)
    s=Store(STATE/'work.db');collector=Collector(s)
    try:
        enabled=selected_sources()
        requested=sys.argv[1:] or enabled
        if not requested:raise ValueError('No sources enabled in the config record')
        if any(name not in enabled for name in requested):raise ValueError('Source is not enabled in the config record')
        for name in requested:
            print(json.dumps({'source':name,**collector.collect(name)}),flush=True)
    finally:s.close()
