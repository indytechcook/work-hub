"""Bounded, outbound-only reads for explicit source references. No arbitrary URL fetch."""
import json
import os
import re
import subprocess
import time
from references import reference
from source_preview import slack_messages
from settings import selected_sources, slack as slack_settings


def command(args, project_id=None):
    result=subprocess.run(args,text=True,capture_output=True,timeout=35,env={**os.environ,**({'BB_PROJECT_ID':project_id} if project_id else {})})
    if result.returncode:raise RuntimeError('Referenced source read failed')
    if len(result.stdout)>500000:raise ValueError('Referenced source response too large')
    return result.stdout


def check_source_scope(ref):
    if ref['source']!='bb' and ref['source'] not in selected_sources():
        raise ValueError('Source is not enabled')
    if ref['source']=='slack':
        from urllib.parse import urlparse
        if urlparse(ref['url']).hostname!=urlparse(slack_settings()['workspace_url']).hostname:
            raise ValueError('Slack workspace is not configured')


def read_reference(ref, project_id=None):
    source=ref['source']
    check_source_scope(ref)
    if source=='slack':
        from slack import call
        response=call('slack_read_thread',{'channel_id':ref['channel'],'message_ts':ref['stamp'],'limit':100,'response_format':'detailed'})
        if response.get('isError'):raise RuntimeError('Slack source read failed')
        content=[b['text'] for b in response.get('content',[]) if b.get('type')=='text']
        body='\n'.join(content)
        message_text=slack_messages(content[0]) if content else ''
        parent=re.search(r'^Message TS: ([0-9]+\.[0-9]{6})',message_text,re.M)
        if parent:
            ref={**ref,'stamp':parent[1],'url':ref['url'].split('/p')[0]+'/p'+parent[1].replace('.','')}
        if not body:raise ValueError('Slack returned no evidence')
        # Keep the original receipt when it fits. For a long page take a
        # bounded excerpt of the decoded messages instead of cutting JSON.
        if len(body)>58000 and message_text:
            body=message_text[:57950]+'\n[Slack thread excerpt truncated]'
        return {'external_id':ref['channel']+':'+ref['stamp'],'title':'Slack conversation','text':body,'url':ref['url']}
    if source=='github':
        if ref['kind']=='pull':
            body=json.loads(command(['gh','pr','view',str(ref['number']),'--repo',ref['repo'],'--json','id,title,body,state,mergedAt,closedAt,updatedAt,headRefOid,reviews,comments,url,reviewDecision,statusCheckRollup']))
            actor=json.loads(command(['gh','api','user']))['login']
            body['authenticated_reviewer']=actor
            return {'external_id':body['id'],'title':body['title'],'text':json.dumps(body),'url':ref['url']}
        body=json.loads(command(['gh','api',f"repos/{ref['repo']}/issues/{ref['number']}"]))
        return {'external_id':str(body['node_id']),'title':body['title'],'text':json.dumps(body),'url':ref['url']}
    if source=='linear':
        body=command(['bb','linear','issue',ref['id'],'--comments'],project_id)
        # A URL from another workspace must never select the same issue identifier here.
        match=re.search(r'^URL: (https://\S+)',body,re.M)
        if not match or not reference(match[1]) or reference(match[1])['key']!=ref['key']:raise ValueError('Linear workspace mismatch')
        return {'external_id':ref['id'],'title':body.splitlines()[0][:500],'text':body,'url':ref['url']}
    if source=='bb':
        out=json.loads(command(['bb','thread','output',ref['id'],'--json']))
        body=json.dumps({'coverage':'Latest assistant output only; verify claims against linked sources when needed','assistant':out.get('output','')})
        return {'external_id':ref['id'],'title':'Related BB conversation','text':body,'url':ref['url']}
    raise ValueError('No targeted reader for source')


def refresh_reference(store, url, project_id=None, *, retry_error=False):
    ref=reference(url)
    if ref is None:raise ValueError('Unsupported source reference')
    cache=store._record('source_read:'+ref['key'],{})
    same_scope=ref['source']!='linear' or ('project_id' in cache and cache['project_id']==project_id)
    if same_scope and cache.get('checked_at',0)>time.time()-120 and not (retry_error and cache.get('error')):
        check_source_scope(ref)
        if cache.get('error'):raise RuntimeError('Referenced source temporarily unavailable')
        if store.db.execute('SELECT 1 FROM conversations WHERE id=?',(cache.get('conversation_id'),)).fetchone():return cache['conversation_id']
    try:
        result=read_reference(ref,project_id)
        # Use the existing source identity when its canonical URL is already known.
        previous=store.db.execute('SELECT c.external_id FROM source_references r JOIN conversations c ON c.id=r.conversation_id WHERE r.key=? AND c.source=? AND c.url=r.url LIMIT 1',(ref['key'],ref['source'])).fetchone()
        if previous:result['external_id']=previous['external_id']
        result['text']=result['text'][:58000]
        from lifecycle import digest
        # Keep a descriptive source title already supplied by the collector.
        cid=digest(ref['source'],result['external_id'])
        existing=store.db.execute('SELECT title FROM conversations WHERE id=?',(cid,)).fetchone()
        if existing and ref['source'] in ('slack','bb'):result['title']=existing['title']
        result['occurred_at']=time.time()
        store.ingest(ref['source'],[result],scope='Targeted reference read; regular collection checkpoint unchanged',complete=False,targeted=True)
        with store.db:store._put_record('source_read:'+ref['key'],{'checked_at':time.time(),'conversation_id':cid,'project_id':project_id})
        return cid
    except (ValueError,RuntimeError,OSError,subprocess.TimeoutExpired) as error:
        with store.db:store._put_record('source_read:'+ref['key'],{'checked_at':time.time(),'error':type(error).__name__,'project_id':project_id})
        raise


def add_source_link(store, work_id, url):
    if not store.db.execute('SELECT 1 FROM work WHERE id=?',(work_id,)).fetchone():
        raise ValueError('Unknown work item')
    ref=reference(url)
    if not ref or ref['source'] not in ('slack','github','linear'):
        raise ValueError('Use a Slack message, GitHub issue or PR, or Linear issue URL')
    check_source_scope(ref)
    # A mention in another source is not itself the requested source.
    captured=None
    for row in store.db.execute('SELECT c.id,c.url FROM conversations c JOIN source_references r ON c.id=r.conversation_id WHERE r.key=? AND c.source=?',(ref['key'],ref['source'])):
        own=reference(row['url'])
        if own and own['key']==ref['key']:
            captured=row
            break
    try:
        cid=captured['id'] if captured else refresh_reference(store,ref['url'])
    except (RuntimeError,OSError,subprocess.TimeoutExpired,ValueError) as error:
        raise ValueError('Could not read this link. Check source access and retry.') from error
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        already=work_id in store._work_ids(cid)
        if not already:
            if captured:
                # Explicit attachment of existing work is additive.
                store.db.execute('INSERT OR IGNORE INTO conversation_work_links VALUES (?,?)',(cid,work_id))
                store.change('association',cid,'add: '+work_id)
            else:
                store.attach_read_source(cid,work_id)
        if ref['source']=='slack':
            scope='work:'+work_id
            store.db.execute("""INSERT OR IGNORE INTO context_requests(thread_id,reference_key,url,state,updated_at)
                VALUES (?,?,?,'ready',?)""",(scope,ref['key'],ref['url'],time.time()))
            store.queue_slack_links(conversation_id=cid,thread_id=scope)
    return {'state':'already_linked' if already else 'added'}


def hydrate_context(store, thread_id=None, limit=1):
    for _ in range(min(limit,3)):
        claim=store.claim_context(thread_id)
        if not claim:break
        try:
            # A collected PR already has a stable GitHub identity. Reuse it
            # for a Slack association; explicit refreshes still read upstream.
            captured=None
            if claim['slack_work_id'] and claim['reference_key'].startswith('github:'):
                check_source_scope(reference(claim['url']))
                captured=store.db.execute("""SELECT c.id FROM conversations c JOIN source_references r
                    ON c.id=r.conversation_id AND c.url=r.url WHERE c.source='github' AND r.key=?""",
                    (claim['reference_key'],)).fetchone()
            cid=captured['id'] if captured else refresh_reference(store,claim['url'],store._record('context_project:'+claim['thread_id'],None))
            store.finish_context(claim,cid)
        except Exception as error:
            # Connector failures can wrap private payloads: retain only a safe category.
            store.finish_context(claim,error='Source read unavailable ('+type(error).__name__+'); retrying')
    return store.context_packet(thread_id) if thread_id else {'ok':True}
