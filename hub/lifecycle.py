"""Durable source linking and completion review state, independent of AI provider."""
import hashlib
import datetime as dt
import json
import re
import time
import uuid
from references import references, reference
from source_preview import slack_messages
from routing import OB_IN_WORK

SCHEMA='''
CREATE TABLE IF NOT EXISTS source_references(key TEXT NOT NULL,conversation_id TEXT NOT NULL,url TEXT NOT NULL,source TEXT NOT NULL,PRIMARY KEY(key,conversation_id));
CREATE INDEX IF NOT EXISTS source_references_conversation ON source_references(conversation_id);
CREATE TABLE IF NOT EXISTS context_requests(thread_id TEXT NOT NULL,reference_key TEXT NOT NULL,url TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',error TEXT,updated_at REAL NOT NULL,lease_until REAL,lease_token TEXT,slack_work_id TEXT,PRIMARY KEY(thread_id,reference_key));
CREATE TABLE IF NOT EXISTS completion_reviews(obligation_id TEXT PRIMARY KEY,state TEXT NOT NULL DEFAULT 'queued',requested_at REAL NOT NULL,checked_at REAL,lease_until REAL,lease_token TEXT,not_before REAL NOT NULL DEFAULT 0,obligation_version INTEGER,fingerprint TEXT,verdict TEXT,reason TEXT,evidence TEXT NOT NULL DEFAULT '[]',error TEXT,applied_version INTEGER);
CREATE TABLE IF NOT EXISTS completion_audit(id INTEGER PRIMARY KEY,obligation_id TEXT NOT NULL,verdict TEXT NOT NULL,reason TEXT NOT NULL,evidence TEXT NOT NULL,checked_at REAL NOT NULL,obligation_version INTEGER NOT NULL);
'''
SLACK_LINKS="""
SELECT q.thread_id,m.work_id,r.key,r.url
FROM context_requests q
LEFT JOIN conversations b ON b.source='bb' AND b.external_id=q.thread_id
JOIN work_memberships m ON m.work_id=CASE WHEN q.thread_id LIKE 'work:%' THEN substr(q.thread_id,6) ELSE b.work_id END
JOIN conversations s ON s.id=m.conversation_id AND s.source='slack'
LEFT JOIN meta cache ON cache.key='source_read:' || q.reference_key
JOIN source_references r ON r.conversation_id=s.id AND r.source IN ('linear','github')
WHERE q.state='ready' AND q.reference_key LIKE 'slack:%'
  AND (q.url=s.url OR json_extract(cache.value,'$.conversation_id')=s.id)
"""
VERDICTS={'confirmed_complete','partly_complete','still_open','upcoming','superseded','unconfirmed'}


def digest(*parts):return hashlib.sha256(json.dumps(parts,sort_keys=True).encode()).hexdigest()[:32]


class LifecycleMixin:
    def index_references(self, conversation_id, *, repair=False):
        row=self.db.execute('SELECT c.source,c.url,e.text FROM conversations c LEFT JOIN events e ON e.id=c.latest_event WHERE c.id=?',(conversation_id,)).fetchone()
        if row is None:return
        body=row['text'] or ''
        if row['source']=='slack':
            # A Slack link's label may itself look like a different destination.
            body=re.sub(r'<(https?://[^|<>\s]+)\|[^<>]*>',r'<\1>',slack_messages(body))
        refs=references((row['url'] or '')+'\n'+body)
        if repair:
            indexed = {tuple(r) for r in self.db.execute(
                'SELECT key,url,source FROM source_references WHERE conversation_id=?',(conversation_id,))}
            if indexed == {(r['key'],r['url'],r['source']) for r in refs}:
                return False
        self.db.execute('DELETE FROM source_references WHERE conversation_id=?',(conversation_id,))
        for ref in refs:self.db.execute('INSERT OR IGNORE INTO source_references VALUES (?,?,?,?)',(ref['key'],conversation_id,ref['url'],ref['source']))
        if self._record('completion_auto_enabled',False):
            self.db.execute("UPDATE completion_reviews SET state='queued',requested_at=? WHERE state='done' AND obligation_id IN (SELECT o.id FROM obligations o WHERE o.state IN ('open','needs_review') AND (o.conversation_id=? OR o.conversation_id IN (SELECT r.conversation_id FROM source_references r JOIN source_references changed ON r.key=changed.key WHERE changed.conversation_id=?)))",(time.time(),conversation_id,conversation_id))

        return True

    def queue_slack_links(self, *, conversation_id=None, thread_id=None, request_key=None):
        # Only expand Slack reads explicitly requested by this thread and still
        # attached to its work. The read cache also maps reply URLs to parents.
        self.db.execute("""
            INSERT OR IGNORE INTO context_requests(thread_id,slack_work_id,reference_key,url,updated_at)
            SELECT related.*, :now FROM (""" + SLACK_LINKS + """
                AND (:cid IS NULL OR s.id=:cid) AND (:tid IS NULL OR q.thread_id=:tid)
                AND (:key IS NULL OR q.reference_key=:key)
            ) related
        """,dict(now=time.time(),cid=conversation_id,tid=thread_id,key=request_key))

    def _discard_stale_slack_request(self, request):
        if request['slack_work_id'] is None:return False
        match=self.db.execute(SLACK_LINKS + """
            AND m.work_id=? AND q.thread_id=? AND r.key=? LIMIT 1
        """,(request['slack_work_id'],request['thread_id'],request['reference_key'])).fetchone()
        if match:return False
        self.db.execute('DELETE FROM context_requests WHERE thread_id=? AND reference_key=?',
                        (request['thread_id'],request['reference_key']))
        return True

    def ensure_context(self, thread_id, title, prompt, model='', parent_id=None, project_id=None, record_prompt=True):
        if type(record_prompt)!=bool:raise ValueError('Invalid prompt capture setting')
        if not isinstance(thread_id,str) or not thread_id.startswith('thr_') or len(thread_id)>100:raise ValueError('Invalid thread')
        if not isinstance(prompt,str) or len(prompt)>60000:raise ValueError('Prompt too large')
        if not isinstance(title,str) or len(title)>1000:raise ValueError('Invalid title')
        if project_id is not None and (not isinstance(project_id,str) or not re.fullmatch(r'proj_[a-z0-9]+',project_id)):raise ValueError('Invalid BB project')
        cid=digest('bb',thread_id);now=time.time();refs=references(prompt)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            old=self.db.execute('SELECT work_id FROM conversations WHERE id=?',(cid,)).fetchone()
            explicit=self.db.execute('SELECT work_id FROM manual_links WHERE conversation_id=?',(cid,)).fetchone()
            parent=self.db.execute("SELECT work_id FROM conversations WHERE source='bb' AND external_id=?",(parent_id,)).fetchone() if parent_id else None
            candidates=set()
            for ref in refs:
                matches=self.db.execute("SELECT DISTINCT m.work_id,c.source,c.external_id FROM source_references r JOIN conversations c ON c.id=r.conversation_id JOIN work_memberships m ON m.conversation_id=r.conversation_id JOIN work w ON w.id=m.work_id WHERE r.key=? AND w.status!='done' AND r.conversation_id<>?",(ref['key'],cid))
                candidates.update(r['work_id'] for r in matches if not self.is_tracking(r['source'],r['external_id']))
            repeated=bool(re.search(r'\b(again|another|repeat|redo|re-run)\b',prompt,re.I))
            if repeated and not old and candidates:
                # A new explicit occurrence must not reuse an already fulfilled request.
                previous=candidates.copy()
                candidates={wid for wid in candidates if not self.db.execute("SELECT 1 FROM obligations o JOIN work_memberships m ON m.conversation_id=o.conversation_id WHERE m.work_id=? AND o.state IN ('fulfilled','cancelled') AND "+OB_IN_WORK+" LIMIT 1",(wid,)).fetchone()}
                self._put_record('prior_context:'+thread_id,sorted(previous-candidates))
            # New root conversations only auto-select an unambiguous exact match.
            wid=explicit['work_id'] if explicit else old['work_id'] if old else parent['work_id'] if parent else next(iter(candidates)) if len(candidates)==1 else cid
            self.db.execute('INSERT OR IGNORE INTO work(id,title,updated_at) VALUES (?,?,?)',(wid,title or prompt[:160] or 'New conversation',now))
            self.db.execute("INSERT OR IGNORE INTO conversations(id,source,external_id,title,url,model,work_id,updated_at) VALUES (?,'bb',?,?,?,?,?,?)",(cid,thread_id,title or prompt[:160],'bb-thread:'+thread_id,model,wid,now))
            # Save the request immediately, without advancing the BB collector checkpoint.
            eid=digest(cid,'request',prompt)
            inserted=self.db.execute("INSERT OR IGNORE INTO events(id,conversation_id,version,text,occurred_at,received_at) VALUES (?,?,?,?,?,?)",(eid,cid,'request:'+digest(prompt),prompt,now,now)).rowcount if record_prompt or not old else 0
            if inserted:
                self.db.execute('UPDATE conversations SET latest_event=?,updated_at=? WHERE id=?',(eid,now,cid))
                self.index_references(cid)
                self.change('context_linked',wid,'Conversation linked; source context is being checked')
            for ref in refs:
                if ref['key']=='bb:'+thread_id:continue
                self.db.execute("""INSERT INTO context_requests(thread_id,reference_key,url,updated_at) VALUES (?,?,?,?)
                    ON CONFLICT(thread_id,reference_key) DO UPDATE SET slack_work_id=NULL""",(thread_id,ref['key'],ref['url'],now))
                if ref['source']=='slack':
                    self.queue_slack_links(thread_id=thread_id,request_key=ref['key'])
            self._put_record('context_candidates:'+thread_id,sorted(candidates-{wid}))
            if project_id:self._put_record('context_project:'+thread_id,project_id)
        return self.context_packet(thread_id)

    def context_packet(self, thread_id):
        context=self.thread_context(thread_id)
        sources=[]
        if context['work']:
            for c in context['work']['conversations'][:15]:
                row=self.db.execute('SELECT text FROM events WHERE id=(SELECT latest_event FROM conversations WHERE id=?)',(c['id'],)).fetchone()
                sources.append({'source':c['source'],'url':c['url'],'title':c['title'],'text':row['text'][:1800] if row else ''})
        requests=[dict(r) for r in self.db.execute('SELECT url,state,error FROM context_requests WHERE thread_id=? ORDER BY updated_at DESC LIMIT 30',(thread_id,))]
        return {'return_points':self.context_handoffs(context['work'],thread_id),'thread_id':thread_id,'work':({'id':context['work']['id'],'title':context['work']['title'],'next_action':context['work']['intent']['next_action'] or context['work']['next_action']} if context['work'] else None),'associated_work':context['associated_work'],'commitments':context['obligations'][:20],'sources':sources,'requests':requests,'candidates':self._record('context_candidates:'+thread_id,[]),'prior_work':self._record('prior_context:'+thread_id,[]),'untrusted_source_data':True}

    def claim_context(self, thread_id=None):
        now=time.time()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            while True:
                row=self.db.execute("SELECT * FROM context_requests WHERE (state='pending' OR (state='running' AND lease_until<?) OR (state='error' AND updated_at<?)) AND (? IS NULL OR thread_id=?) ORDER BY updated_at LIMIT 1",(now,now-300,thread_id,thread_id)).fetchone()
                if not row:return None
                if not self._discard_stale_slack_request(row):break
            token=str(uuid.uuid4())
            self.db.execute("UPDATE context_requests SET state='running',lease_token=?,lease_until=? WHERE thread_id=? AND reference_key=?",(token,now+180,row['thread_id'],row['reference_key']))
            return {**dict(row),'lease_token':token}

    def finish_context(self, claim, source_conversation_id=None, error=None, gone=False):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute('SELECT * FROM context_requests WHERE thread_id=? AND reference_key=? AND lease_token=?',(claim['thread_id'],claim['reference_key'],claim['lease_token'])).fetchone()
            if not row or self._discard_stale_slack_request(row):return False
            current=self.db.execute("SELECT work_id FROM conversations WHERE source='bb' AND external_id=?",(claim['thread_id'],)).fetchone()
            if claim['thread_id'].startswith('work:'):
                current=self.db.execute('SELECT id work_id FROM work WHERE id=?',(claim['thread_id'][5:],)).fetchone()
            if current and source_conversation_id:
                self.attach_read_source(source_conversation_id,current['work_id'])
            self.db.execute('UPDATE context_requests SET state=?,error=?,updated_at=?,lease_token=NULL,lease_until=NULL WHERE thread_id=? AND reference_key=?',('gone' if gone else 'error' if error else 'ready',error,time.time(),claim['thread_id'],claim['reference_key']))
            if source_conversation_id and not error and claim['reference_key'].startswith('slack:'):
                self.queue_slack_links(conversation_id=source_conversation_id,thread_id=claim['thread_id'])
        return True

    def fold_source_card(self, cid, destination):
        """Move the primary link only while the source still owns an untouched card."""
        moved = self.db.execute("""
            UPDATE conversations SET work_id=? WHERE id=? AND work_id=id AND id<>?
            AND EXISTS(SELECT 1 FROM work w WHERE w.id=conversations.id AND w.status='active' AND w.focus=0
                AND NOT EXISTS(SELECT 1 FROM manual_links WHERE conversation_id=w.id)
                AND NOT EXISTS(SELECT 1 FROM obligations WHERE conversation_id=w.id)
                AND NOT EXISTS(SELECT 1 FROM work_memberships WHERE work_id=w.id AND conversation_id<>w.id)
                AND NOT EXISTS(SELECT 1 FROM meta WHERE key IN ('intent:'||w.id,'work_name:'||w.id))
                AND NOT EXISTS(SELECT 1 FROM meta p,json_each(p.value,'$.work_ids') j
                               WHERE p.key LIKE 'day_plan:%' AND j.value=w.id))
        """, (destination,cid,destination))
        return moved.rowcount == 1

    def attach_read_source(self, source_conversation_id, destination):
        own=self.db.execute('SELECT work_id FROM conversations WHERE id=?',(source_conversation_id,)).fetchone()
        if own and own['work_id']!=destination and not self.fold_source_card(source_conversation_id,destination):
            self.db.execute('INSERT OR IGNORE INTO conversation_work_links VALUES (?,?)',(source_conversation_id,destination))
        self.change('context_ready',destination,'Referenced source context loaded')

    def queue_completion(self, scope='unseen', ids=None):
        if scope not in ('unseen','open','selected'):raise ValueError('Unknown review scope')
        if ids is not None and (not isinstance(ids,list) or len(ids)>100 or any(not isinstance(i,str) or len(i)>100 for i in ids)):raise ValueError('Invalid commitment selection')
        unseen={r['entity_id'] for r in self.attention() if r['kind']=='obligation' and not r['seen']} if scope=='unseen' else set()
        selected=set(ids or []);now=time.time();count=0
        with self.db:
            for row in self.db.execute("SELECT id,version FROM obligations WHERE state IN ('open','needs_review')").fetchall():
                if self.is_legacy_obligation(row['id']):continue
                if (scope=='unseen' and row['id'] not in unseen) or (scope=='selected' and row['id'] not in selected):continue
                self.db.execute("INSERT INTO completion_reviews(obligation_id,requested_at) VALUES (?,?) ON CONFLICT(obligation_id) DO UPDATE SET state=CASE WHEN completion_reviews.state='running' THEN 'running' ELSE 'queued' END,requested_at=excluded.requested_at,not_before=0,error=NULL",(row['id'],now));count+=1
        return {'queued':count}

    def completion_page(self, offset=0, verdict='all'):
        if type(offset)!=int or offset<0 or offset>100000:raise ValueError('Invalid offset')
        if verdict not in VERDICTS|{'all','pending'}:raise ValueError('Invalid verdict')
        args=(verdict,verdict,verdict)
        total=self.db.execute("SELECT count(*) FROM completion_reviews r WHERE (?='all' OR (?='pending' AND r.state IN ('queued','running','error')) OR r.verdict=?)",args).fetchone()[0]
        rows=[dict(r) for r in self.db.execute('SELECT r.*,o.title,o.state commitment_state,o.version current_version,o.work_id,c.url FROM completion_reviews r JOIN obligations o ON o.id=r.obligation_id JOIN conversations c ON c.id=o.conversation_id WHERE (?="all" OR (?="pending" AND r.state IN ("queued","running","error")) OR r.verdict=?) ORDER BY r.requested_at DESC,r.obligation_id LIMIT 50 OFFSET ?',(*args,offset))]
        for row in rows:row['evidence']=json.loads(row['evidence'])
        counts={r['state']:r['n'] for r in self.db.execute('SELECT state,count(*) n FROM completion_reviews GROUP BY state')}
        from storage import Store as SettingsStore
        from worker import budget
        policy=SettingsStore(self.path.parent).read('policy') or {'max_analyses_per_day':120}
        limit,_=budget(self,policy,time.time())
        key='analysis_runs:'+dt.datetime.now(dt.timezone.utc).date().isoformat()
        used=int(self._record(key,0))
        return {'items':rows,'total':total,'has_more':offset+len(rows)<total,'counts':counts,'budget':{'limit':limit,'used':used,'remaining':max(0,limit-used)}}

    def claim_completion(self):
        now=time.time()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute("SELECT r.obligation_id FROM completion_reviews r JOIN obligations o ON o.id=r.obligation_id WHERE o.state IN ('open','needs_review') AND r.not_before<=? AND (r.state IN ('queued','error') OR (r.state='running' AND r.lease_until<?)) ORDER BY r.requested_at",(now,now)).fetchall()
            row=next((r for r in row if not self.is_legacy_obligation(r[0])),None)
            if not row:return None
            token=str(uuid.uuid4());obligation=dict(self.db.execute('SELECT * FROM obligations WHERE id=?',(row[0],)).fetchone())
            self.db.execute("UPDATE completion_reviews SET state='running',lease_token=?,lease_until=?,obligation_version=? WHERE obligation_id=?",(token,now+600,obligation['version'],row[0]))
            return {'obligation':obligation,'lease_token':token}

    def completion_evidence(self, obligation):
        cids={obligation['conversation_id']}
        work_ids=self._work_ids(obligation['conversation_id'])
        for wid in work_ids:cids.update(r[0] for r in self.db.execute('SELECT conversation_id FROM work_memberships WHERE work_id=?',(wid,)))
        keys=[r[0] for r in self.db.execute('SELECT key FROM source_references WHERE conversation_id=?',(obligation['conversation_id'],))]
        for key in keys:cids.update(r[0] for r in self.db.execute('SELECT conversation_id FROM source_references WHERE key=? LIMIT 50',(key,)))
        evidence=[]
        for cid in sorted(cids):
            row=self.db.execute('SELECT e.id,e.text,e.occurred_at,c.url,c.source FROM conversations c JOIN events e ON e.id=c.latest_event WHERE c.id=?',(cid,)).fetchone()
            if row:evidence.append(dict(row))
        # Prefer recent evidence and keep a hard context budget.
        evidence.sort(key=lambda e:e['occurred_at'],reverse=True)
        return [{**e,'text':e['text'][:8000]} for e in evidence[:12]]

    def finish_completion(self, claim, verdict, reason, evidence, fingerprint, basis=None):
        if verdict not in VERDICTS or not isinstance(reason,str) or len(reason)>3000:raise ValueError('Invalid assessment')
        oid=claim['obligation']['id'];now=time.time()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute('SELECT * FROM completion_reviews WHERE obligation_id=? AND lease_token=?',(oid,claim['lease_token'])).fetchone()
            current=self.db.execute('SELECT version FROM obligations WHERE id=?',(oid,)).fetchone()
            if not row:return {'applied':False}
            if current['version']!=claim['obligation']['version']:
                self.db.execute("UPDATE completion_reviews SET state='queued',lease_token=NULL,error='Commitment changed; reviewing again' WHERE obligation_id=?",(oid,));return {'applied':False}
            current_basis=sorted(e['id'] for e in self.completion_evidence(claim['obligation']))
            if basis is not None and current_basis!=sorted(basis):
                self.db.execute("UPDATE completion_reviews SET state='queued',lease_token=NULL,error='Evidence changed; reviewing again' WHERE obligation_id=?",(oid,));return {'applied':False}
            self._put_record('completion_basis:'+oid,current_basis)
            encoded=json.dumps(evidence)
            self.db.execute("UPDATE completion_reviews SET state='done',checked_at=?,fingerprint=?,verdict=?,reason=?,evidence=?,error=NULL,lease_token=NULL WHERE obligation_id=?",(now,fingerprint,verdict,reason,encoded,oid))
            self.db.execute('INSERT INTO completion_audit(obligation_id,verdict,reason,evidence,checked_at,obligation_version) VALUES (?,?,?,?,?,?)',(oid,verdict,reason,encoded,now,current['version']))
            self.change('completion_review',oid,verdict.replace('_',' '))
        return {'applied':True}

    def resolve_completion(self, obligation_id):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.is_legacy_obligation(obligation_id):raise ValueError('Reconcile this legacy commitment before applying completion')
            row=self.db.execute("SELECT * FROM completion_reviews WHERE obligation_id=? AND state='done' AND verdict='confirmed_complete'",(obligation_id,)).fetchone()
            if not row:raise ValueError('No confirmed completion to apply')
            evidence=json.loads(row['evidence'])
            obligation=dict(self.db.execute('SELECT * FROM obligations WHERE id=?',(obligation_id,)).fetchone())
            basis=sorted(e['id'] for e in self.completion_evidence(obligation))
            if not evidence or basis!=self._record('completion_basis:'+obligation_id,[]):raise ValueError('Evidence changed; check completion again')
            # A newer source receipt invalidates a saved recommendation.
            for item in evidence:
                valid=self.db.execute('SELECT 1 FROM conversations WHERE latest_event=?',(item['event_id'],)).fetchone()
                if not valid:raise ValueError('Evidence changed; check completion again')
            note=row['reason']+' Evidence: '+' '.join(e['url'] for e in evidence)
            changed=self.db.execute("UPDATE obligations SET state='fulfilled',resolution=?,version=version+1,updated_at=? WHERE id=? AND version=? AND state IN ('open','needs_review')",(note[:3000],time.time(),obligation_id,row['obligation_version'])).rowcount
            if not changed:raise ValueError('Commitment changed; check completion again')
            self.db.execute('UPDATE completion_reviews SET applied_version=? WHERE obligation_id=?',(row['obligation_version']+1,obligation_id))
            self.change('user_action',obligation_id,'Verified completion applied')
        return {'ok':True}
