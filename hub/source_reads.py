"""Bounded, outbound-only reads for explicit source references. No arbitrary URL fetch."""
import json
import os
import re
import subprocess
import time
from references import reference
from settings import selected_sources, slack as slack_settings


def command(args, project_id=None):
    result=subprocess.run(args,text=True,capture_output=True,timeout=35,env={**os.environ,**({'BB_PROJECT_ID':project_id} if project_id else {})})
    if result.returncode:raise RuntimeError('Referenced source read failed')
    if len(result.stdout)>500000:raise ValueError('Referenced source response too large')
    return result.stdout


def read_reference(ref, project_id=None):
    source=ref['source']
    if source!='bb' and source not in selected_sources():raise ValueError('Source is not enabled')
    if source=='slack':
        from slack import call
        from urllib.parse import urlparse
        if urlparse(ref['url']).hostname!=urlparse(slack_settings()['workspace_url']).hostname:raise ValueError('Slack workspace is not configured')
        response=call('slack_read_thread',{'channel_id':ref['channel'],'message_ts':ref['stamp'],'limit':100,'response_format':'detailed'})
        if response.get('isError'):raise RuntimeError('Slack source read failed')
        content=[b['text'] for b in response.get('content',[]) if b.get('type')=='text']
        body='\n'.join(content)
        try:
            parsed=json.loads(content[0])
            message_text=parsed.get('messages','')
        except (ValueError,IndexError,AttributeError):message_text=body
        parent=re.search(r'^Message TS: ([0-9]+\.[0-9]{6})',message_text,re.M)
        if parent:
            ref={**ref,'stamp':parent[1],'url':ref['url'].split('/p')[0]+'/p'+parent[1].replace('.','')}
        if not body:raise ValueError('Slack returned no evidence')
        # Preserve the whole returned bounded page, including pagination warnings.
        return {'external_id':ref['channel']+':'+ref['stamp'],'title':'Slack conversation','text':body,'url':ref['url']}
    if source=='github':
        if ref['kind']=='pull':
            body=json.loads(command(['gh','pr','view',str(ref['number']),'--repo',ref['repo'],'--json','id,title,body,state,mergedAt,closedAt,updatedAt,headRefOid,reviews,comments,url']))
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


def refresh_reference(store, url, project_id=None):
    ref=reference(url)
    if ref is None:raise ValueError('Unsupported source reference')
    cache=store._record('source_read:'+ref['key'],{})
    if cache.get('checked_at',0)>time.time()-120:
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
        with store.db:store._put_record('source_read:'+ref['key'],{'checked_at':time.time(),'conversation_id':cid})
        return cid
    except (ValueError,RuntimeError,OSError,subprocess.TimeoutExpired) as error:
        with store.db:store._put_record('source_read:'+ref['key'],{'checked_at':time.time(),'error':type(error).__name__})
        raise


def hydrate_context(store, thread_id=None, limit=1):
    for _ in range(min(limit,3)):
        claim=store.claim_context(thread_id)
        if not claim:break
        try:
            cid=refresh_reference(store,claim['url'],store._record('context_project:'+claim['thread_id'],None))
            store.finish_context(claim,cid)
        except Exception as error:
            # Connector failures can wrap private payloads: retain only a safe category.
            store.finish_context(claim,error='Source read unavailable ('+type(error).__name__+'); retrying')
    return store.context_packet(thread_id) if thread_id else {'ok':True}
