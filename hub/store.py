"""Portable work ledger. No BB or model dependency; all mutations are transactional."""
import hashlib
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime
from settings import timezone as local_timezone
from pathlib import Path
from presentation import PresentationMixin
from tracking import TrackingMixin
from handoffs import HandoffMixin
from source_preview import slack_preview
from labels import LabelMixin, slack_label, slack_search_text

from lifecycle import LifecycleMixin, SCHEMA as LIFECYCLE_SCHEMA

SCHEMA = '''
CREATE TABLE IF NOT EXISTS sources(name TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'not_connected', scope TEXT NOT NULL DEFAULT '', checkpoint TEXT, checked_at REAL, last_success REAL, error TEXT);
CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', parent_id TEXT, work_id TEXT NOT NULL, latest_event TEXT, updated_at REAL NOT NULL, UNIQUE(source, external_id));
CREATE TABLE IF NOT EXISTS work(id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', focus INTEGER NOT NULL DEFAULT 0, summary TEXT NOT NULL DEFAULT '', next_action TEXT NOT NULL DEFAULT '', decisions TEXT NOT NULL DEFAULT '[]', updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, version TEXT NOT NULL, text TEXT NOT NULL, occurred_at REAL NOT NULL, received_at REAL NOT NULL, analysis_state TEXT NOT NULL DEFAULT 'pending', analysis_attempts INTEGER NOT NULL DEFAULT 0, analysis_error TEXT, retry_at REAL NOT NULL DEFAULT 0, lease_until REAL, lease_token TEXT, UNIQUE(conversation_id,version));
CREATE INDEX IF NOT EXISTS events_pending ON events(analysis_state,retry_at,received_at);
CREATE TABLE IF NOT EXISTS obligations(id TEXT PRIMARY KEY, work_id TEXT NOT NULL, conversation_id TEXT NOT NULL, evidence_event TEXT NOT NULL, evidence_quote TEXT NOT NULL, title TEXT NOT NULL, owner TEXT NOT NULL, direction TEXT NOT NULL CHECK(direction IN ('i_owe','they_owe','unclear')), due_at REAL, state TEXT NOT NULL CHECK(state IN ('open','needs_review','fulfilled','cancelled')), resolution TEXT, version INTEGER NOT NULL DEFAULT 1, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS changes(seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, entity_id TEXT NOT NULL, detail TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, kind TEXT NOT NULL, source_key TEXT NOT NULL, revision TEXT NOT NULL, state TEXT NOT NULL, thread_id TEXT, detail TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL, UNIQUE(kind,source_key,revision));
CREATE TABLE IF NOT EXISTS decisions(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, evidence_event TEXT NOT NULL, text TEXT NOT NULL, quote TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS manual_links(conversation_id TEXT PRIMARY KEY, work_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS conversation_work_links(conversation_id TEXT NOT NULL, work_id TEXT NOT NULL, PRIMARY KEY(conversation_id,work_id));
CREATE INDEX IF NOT EXISTS conversation_work_links_work ON conversation_work_links(work_id,conversation_id);
CREATE VIEW IF NOT EXISTS work_memberships AS SELECT id conversation_id,work_id FROM conversations UNION SELECT conversation_id,work_id FROM conversation_work_links;
CREATE TABLE IF NOT EXISTS return_points(conversation_id TEXT PRIMARY KEY, summary TEXT NOT NULL, next_action TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS job_leases(job_id TEXT PRIMARY KEY,token TEXT NOT NULL,lease_until REAL NOT NULL);
CREATE TABLE IF NOT EXISTS reminders(id TEXT PRIMARY KEY, day TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL, finished_at REAL);
CREATE TABLE IF NOT EXISTS reminder_items(reminder_id TEXT NOT NULL, obligation_id TEXT NOT NULL, due_at REAL NOT NULL, day TEXT NOT NULL, UNIQUE(obligation_id,due_at,day));
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
'''
SOURCES = ['bb','gmail','calendar','drive','linear','github','slack','meetings','betterstack']


def digest(*items):
    return hashlib.sha256(json.dumps(items, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:32]


def text(value, limit=2000):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError('Invalid text length/type')
    return value


class Store(LifecycleMixin, PresentationMixin, TrackingMixin, HandoffMixin, LabelMixin):
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(path, timeout=20)
        os.chmod(path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.create_function('slack_search_text',1,slack_search_text,deterministic=True)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript(SCHEMA+LIFECYCLE_SCHEMA)
        with self.db:
            for source in SOURCES:
                self.db.execute('INSERT OR IGNORE INTO sources(name) VALUES (?)', (source,))
            if not self._record('reference_index_v1',False) and self.db.execute('SELECT 1 FROM conversations LIMIT 1').fetchone():
                for row in self.db.execute('SELECT id FROM conversations').fetchall():self.index_references(row[0])
                self._put_record('reference_index_v1',True)

    def close(self):
        self.db.close()

    def change(self, kind, entity, detail):
        self.db.execute('INSERT INTO changes(kind,entity_id,detail,at) VALUES (?,?,?,?)',
                        (kind, entity, detail, time.time()))

    def ingest(self, source, items, checkpoint=None, scope='', complete=True, targeted=False):
        if source not in SOURCES or not isinstance(items, list) or len(items) > 500:
            raise ValueError('Invalid source batch')
        now = time.time()
        inserted = 0
        with self.db:
            for item in items:
                external = text(item['external_id'], 1000)
                cid = digest(source, external)
                title = text(item['title'], 1000)
                body = text(item.get('text',''), 60000)
                version = text(item.get('version') or digest(title, body), 200)
                occurred = float(item.get('occurred_at', now))
                if not (0 <= occurred <= now + 86400):
                    raise ValueError('Invalid event time')
                url = text(item.get('url',''), 2000)
                if url and not url.startswith(('https://','bb-thread:')):
                    raise ValueError('Unsupported source link')
                parent = digest(source, item['parent_id']) if item.get('parent_id') else None
                old = self.db.execute('SELECT * FROM conversations WHERE id=?', (cid,)).fetchone()
                pw = self.db.execute('SELECT work_id FROM conversations WHERE id=?', (parent,)).fetchone() if parent else None
                wid = old['work_id'] if old else (pw['work_id'] if pw else cid)
                self.db.execute('INSERT OR IGNORE INTO work(id,title,updated_at) VALUES (?,?,?)', (wid,title,now))
                eid = digest(cid, version)
                cursor = self.db.execute('INSERT OR IGNORE INTO events(id,conversation_id,version,text,occurred_at,received_at) VALUES (?,?,?,?,?,?)',
                                        (eid,cid,version,body,occurred,now))
                if cursor.rowcount:
                    inserted += 1
                    previous = self.db.execute('SELECT occurred_at FROM events WHERE id=?', (old['latest_event'],)).fetchone() if old else None
                    if previous and previous['occurred_at'] > occurred:
                        self.db.execute("UPDATE events SET analysis_state='superseded' WHERE id=?", (eid,))
                        continue
                    self.db.execute("UPDATE events SET analysis_state='superseded',lease_token=NULL WHERE conversation_id=? AND id<>? AND analysis_state IN ('pending','running')", (cid,eid))
                    self.db.execute('''INSERT INTO conversations(id,source,external_id,title,url,model,parent_id,work_id,latest_event,updated_at)
                      VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,url=excluded.url,model=excluded.model,parent_id=excluded.parent_id,latest_event=excluded.latest_event,updated_at=excluded.updated_at''',
                      (cid,source,external,title,url,text(item.get('model',''),200),parent,wid,eid,now))
                    self.db.execute('UPDATE work SET updated_at=? WHERE id=?', (now,wid))
                    self.index_references(cid)
                    self.change('received',cid,title)
                    if source=='bb':self.capture_handoff(cid,body,occurred)
                    if self.is_tracking(source,external):
                        self.db.execute("UPDATE events SET analysis_state='reference_only' WHERE id=?",(eid,))
                    if source=='drive':
                        self.db.execute("UPDATE events SET analysis_state='metadata_only' WHERE id=?",(eid,))
            # Reconcile parents that arrived later without undoing explicit manual links.
            self.db.execute('''UPDATE conversations SET work_id=(SELECT p.work_id FROM conversations p WHERE p.id=conversations.parent_id)
                WHERE parent_id IS NOT NULL AND EXISTS(SELECT 1 FROM conversations p WHERE p.id=conversations.parent_id)
                AND NOT EXISTS(SELECT 1 FROM manual_links m WHERE m.conversation_id=conversations.id)
                AND NOT EXISTS(SELECT 1 FROM meta n WHERE n.key='work_name:' || conversations.work_id)''')
            self.db.execute('UPDATE obligations SET work_id=(SELECT c.work_id FROM conversations c WHERE c.id=obligations.conversation_id)')
            state = 'current' if complete else 'partial'
            if not targeted:
                self.db.execute('''UPDATE sources SET state=?,scope=?,checkpoint=CASE WHEN ? THEN ? ELSE checkpoint END,
                     checked_at=?,last_success=CASE WHEN ? THEN ? ELSE last_success END,error=NULL WHERE name=?''',
                     (state,scope,int(complete),json.dumps(checkpoint),now,int(complete),now,source))
        return {'inserted':inserted, 'state':state}

    def source_failure(self, source, message):
        if source not in SOURCES:
            raise ValueError('Unknown source')
        with self.db:
            self.db.execute("UPDATE sources SET state='error',checked_at=?,error=? WHERE name=?",(time.time(),text(message,500),source))
        return {'ok': True}

    def claim_analysis(self):
        now = time.time()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute("UPDATE events SET analysis_state='pending',lease_token=NULL WHERE analysis_state='running' AND lease_until<?", (now,))
            previous=self.db.execute("SELECT value FROM meta WHERE key='last_analysis_source'").fetchone()
            order=['bb','gmail','linear','calendar','slack','meetings','github','betterstack']
            if previous and previous[0] in order:
                index=order.index(previous[0])+1;order=order[index:]+order[:index]
            count_row=self.db.execute("SELECT value FROM meta WHERE key='analysis_claim_count'").fetchone()
            count=int(count_row[0]) if count_row else 0
            direction='ASC' if count%5==4 else 'DESC'
            row=None
            for source in order:
                row=self.db.execute("SELECT e.*,c.title,c.source,c.work_id FROM events e JOIN conversations c ON c.latest_event=e.id WHERE e.analysis_state='pending' AND e.retry_at<=? AND c.source=? ORDER BY e.occurred_at "+direction+",e.received_at "+direction+" LIMIT 1",(now,source)).fetchone()
                if row:break
            if not row:
                return None
            self.db.execute("INSERT INTO meta VALUES ('analysis_claim_count',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(count+1),))
            token = str(uuid.uuid4())
            changed = self.db.execute("UPDATE events SET analysis_state='running',lease_until=?,lease_token=?,analysis_attempts=analysis_attempts+1 WHERE id=? AND analysis_state='pending'", (now+300,token,row['id'])).rowcount
            if not changed:
                return None
            self.db.execute("INSERT INTO meta VALUES ('last_analysis_source',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(row['source'],))
            result = dict(row)
            result['lease_token'] = token
            result['obligations'] = [dict(r) for r in self.db.execute("SELECT id,title,owner,direction,state,due_at FROM obligations WHERE conversation_id=? ORDER BY updated_at DESC LIMIT 100",(row['conversation_id'],))]
            return result

    def analysis_failed(self, event_id, token, error):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute("UPDATE events SET analysis_state='pending',analysis_error=?,retry_at=?,lease_token=NULL WHERE id=? AND lease_token=?",(text(error,500),time.time()+900,event_id,token))
        return {'ok':True}

    def apply_analysis(self, event_id, token, result):
        now = time.time()
        with self.db:
            event = self.db.execute('SELECT e.*,c.work_id,c.latest_event FROM events e JOIN conversations c ON c.id=e.conversation_id WHERE e.id=?',(event_id,)).fetchone()
            if not event or event['lease_token'] != token or event['analysis_state'] != 'running':
                raise ValueError('Analysis lease expired or already consumed')
            if event['latest_event'] != event_id:
                self.db.execute("UPDATE events SET analysis_state='superseded',lease_token=NULL WHERE id=?", (event_id,))
                return {'applied':False,'reason':'newer_source_version'}
            if event['lease_until'] < now:raise ValueError('Analysis lease expired')
            summary = text(result['summary'],3000)
            next_action = text(result['next_action'],1500)
            decisions = result.get('decisions',[])
            if not isinstance(decisions,list) or len(decisions)>15:
                raise ValueError('Invalid decisions')
            # Only evidence-backed accepted decisions, not assistant proposals.
            accepted=[]
            for d in decisions:
                quote=text(d['quote'],2000)
                if not quote or quote not in event['text']:
                    raise ValueError('Decision evidence missing from source')
                decision_text=text(d['text'],1000)
                self.db.execute('INSERT OR IGNORE INTO decisions(id,conversation_id,evidence_event,text,quote) VALUES (?,?,?,?,?)', (digest(event['conversation_id'],decision_text,quote),event['conversation_id'],event_id,decision_text,quote))
            accepted=[dict(r) for r in self.db.execute('SELECT d.text,d.quote,d.evidence_event event_id FROM decisions d JOIN conversations c ON c.id=d.conversation_id WHERE c.work_id=?',(event['work_id'],))]
            self.db.execute('INSERT INTO return_points(conversation_id,summary,next_action,updated_at) VALUES (?,?,?,?) ON CONFLICT(conversation_id) DO UPDATE SET summary=excluded.summary,next_action=excluded.next_action,updated_at=excluded.updated_at', (event['conversation_id'],summary,next_action,now))
            self.db.execute('UPDATE work SET summary=?,next_action=?,decisions=?,updated_at=? WHERE id=?',
                            (summary,next_action,json.dumps(accepted),now,event['work_id']))
            entries = result.get('obligations',[])
            if not isinstance(entries,list) or len(entries)>25:
                raise ValueError('Too many obligations')
            for o in entries:
                quote=text(o['quote'],3000)
                if not quote or quote not in event['text']:
                    raise ValueError('Obligation evidence missing from source')
                title=text(o['title'],1000);owner=text(o['owner'],300)
                direction=o['direction'];state=o['state']
                if direction not in ['i_owe','they_owe','unclear'] or state not in ['open','needs_review','fulfilled','cancelled']:
                    raise ValueError('Invalid obligation classification')
                confidence=float(o['confidence'])
                if not 0<=confidence<=1:raise ValueError('Invalid confidence')
                existing_id=o.get('existing_id')
                old = self.db.execute('SELECT * FROM obligations WHERE id=? AND conversation_id=?',(existing_id,event['conversation_id'])).fetchone() if existing_id else None
                if existing_id and not old:raise ValueError('Resolution points outside conversation')
                if state in ['fulfilled','cancelled'] and (not old or confidence<0.95 or not o.get('explicit_resolution')):
                    raise ValueError('Resolution requires explicit evidence and known obligation')
                if state in ['open','needs_review'] and (confidence<0.9 or direction=='unclear'):
                    state='needs_review'
                due=o.get('due_at')
                if due is not None and (isinstance(due,bool) or not isinstance(due,(int,float)) or due<0):raise ValueError('Invalid due time')
                if not old:
                    old=self.db.execute('SELECT * FROM obligations WHERE conversation_id=? AND evidence_quote=? AND lower(owner)=lower(?) ORDER BY updated_at DESC LIMIT 1',(event['conversation_id'],quote,owner)).fetchone()
                oid=old['id'] if old else digest(event['conversation_id'],title.casefold(),owner.casefold())
                prior=self.db.execute('SELECT * FROM obligations WHERE id=?',(oid,)).fetchone()
                # A repeated old promise must never reopen something resolved by a later message.
                if prior and prior['state'] in ['fulfilled','cancelled'] and state in ['open','needs_review']:
                    continue
                self.db.execute('''INSERT INTO obligations(id,work_id,conversation_id,evidence_event,evidence_quote,title,owner,direction,due_at,state,resolution,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET evidence_event=excluded.evidence_event,evidence_quote=excluded.evidence_quote,
                    title=excluded.title,owner=excluded.owner,direction=excluded.direction,due_at=excluded.due_at,state=excluded.state,
                    resolution=excluded.resolution,version=obligations.version+1,updated_at=excluded.updated_at''',
                    (oid,event['work_id'],event['conversation_id'],event_id,quote,title,owner,direction,due,state,quote if state in ['fulfilled','cancelled'] else None,now))
                self.change('obligation',oid,state+': '+title)
            self.db.execute("UPDATE events SET analysis_state='done',analysis_error=NULL,lease_token=NULL,lease_until=NULL WHERE id=?",(event_id,))
            self._put_record('checkpoint:'+event['conversation_id'],dict(event_id=event_id,occurred_at=event['occurred_at'],received_at=event['received_at'],analyzed_at=now))
            self.change('return_point',event['work_id'],next_action)
        return {'applied':True}

    def mutate(self, kind, id, action, version=None, value=None):
        result={'ok':True}
        with self.db:
            if kind=='work':
                if not self.db.execute('SELECT 1 FROM work WHERE id=?',(id,)).fetchone():raise ValueError('Unknown work')
                if action=='focus':
                    self.db.execute('UPDATE work SET focus=1 WHERE id=?',(id,))
                    result['thread_ids']=[row[0] for row in self.db.execute(
                        """SELECT c.external_id FROM conversations c JOIN work_memberships m ON m.conversation_id=c.id
                           WHERE m.work_id=? AND c.source='bb' ORDER BY c.external_id""", (id,))]
                elif action=='unfocus':
                    self.db.execute('UPDATE work SET focus=0 WHERE id=?',(id,))
                elif action in ['active','waiting','done']:
                    self.db.execute('UPDATE work SET status=?,updated_at=? WHERE id=?',(action,time.time(),id))
                else:raise ValueError('Invalid work action')
            elif kind=='obligation':
                if action not in ['open','fulfilled','cancelled']:raise ValueError('Invalid obligation action')
                note=text(value,3000) if value is not None else None
                changed=self.db.execute('UPDATE obligations SET state=?,resolution=?,version=version+1,updated_at=? WHERE id=? AND version=?',(action,note,time.time(),id,version)).rowcount
                if not changed:raise ValueError('This follow-up changed; refresh before editing')
            elif kind=='conversation' and action=='link':
                if not self.db.execute('SELECT 1 FROM work WHERE id=?',(value,)).fetchone():raise ValueError('Unknown target work')
                if not self.db.execute('UPDATE conversations SET work_id=? WHERE id=?',(value,id)).rowcount:raise ValueError('Unknown conversation')
                self.db.execute('DELETE FROM conversation_work_links WHERE conversation_id=?',(id,))
                self.db.execute('UPDATE obligations SET work_id=? WHERE conversation_id=?',(value,id))
                self.db.execute('INSERT INTO manual_links VALUES (?,?) ON CONFLICT(conversation_id) DO UPDATE SET work_id=excluded.work_id',(id,value))
            else:raise ValueError('Invalid action')
            self.change('user_action',id,action)
        return result

    def register_agent_thread(self, work_id, thread_id, title, model, occurred_at):
        text(work_id,100);text(thread_id,100);text(title,1000);text(model,200)
        if not thread_id.startswith('thr_'):raise ValueError('Expected a BB thread')
        now=time.time();occurred_at=float(occurred_at)
        if not 0<=occurred_at<=now+86400:raise ValueError('Invalid thread time')
        cid=digest('bb',thread_id)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if not self.db.execute('SELECT 1 FROM work WHERE id=?',(work_id,)).fetchone():
                return {'ok':False,'reason':'unknown_work'}
            existing=self.db.execute('SELECT work_id FROM manual_links WHERE conversation_id=?',(cid,)).fetchone()
            target=existing['work_id'] if existing else work_id
            self.db.execute("INSERT INTO conversations(id,source,external_id,title,url,model,work_id,updated_at) VALUES (?,'bb',?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET work_id=excluded.work_id",(cid,thread_id,title,'bb-thread:'+thread_id,model,target,occurred_at))
            self.db.execute('UPDATE obligations SET work_id=? WHERE conversation_id=?',(target,cid))
            self.db.execute('INSERT OR IGNORE INTO manual_links VALUES (?,?)',(cid,target))
            if not existing:
                self.db.execute("UPDATE work SET status='active',updated_at=? WHERE id=?",(now,target))
                self.change('agent_thread',target,'Started linked thread: '+title)
        return {'ok':True}

    def observe_job(self, kind, source_key, revision):
        if kind not in ['fixture_review','betterstack_triage']:raise ValueError('Unknown job kind')
        source_key=text(source_key,500);revision=text(revision,200);jid=digest(kind,source_key,revision);now=time.time()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute("UPDATE jobs SET state='superseded',updated_at=? WHERE kind=? AND source_key=? AND revision<>? AND state IN ('pending','running')",(now,kind,source_key,revision))
            inserted=self.db.execute("INSERT OR IGNORE INTO jobs(id,kind,source_key,revision,state,created_at,updated_at) VALUES (?,?,?,?,'pending',?,?)",(jid,kind,source_key,revision,now,now)).rowcount
            if inserted:self.change('job_pending',jid,kind+': '+source_key)
        return {'id':jid,'inserted':bool(inserted)}

    def claim_job(self, kind):
        now=time.time()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute("UPDATE jobs SET state='pending' WHERE state='running' AND id IN (SELECT job_id FROM job_leases WHERE lease_until<?)",(now,))
            row=self.db.execute("SELECT * FROM jobs WHERE kind=? AND state='pending' ORDER BY created_at LIMIT 1",(kind,)).fetchone()
            if not row:return None
            token=str(uuid.uuid4())
            self.db.execute("UPDATE jobs SET state='running',updated_at=? WHERE id=?",(now,row['id']))
            self.db.execute('INSERT INTO job_leases VALUES (?,?,?) ON CONFLICT(job_id) DO UPDATE SET token=excluded.token,lease_until=excluded.lease_until',(row['id'],token,now+300))  # gitleaks:allow — generated UUID lease, not a credential literal.
            return dict(row,lease_token=token)

    def finish_job(self, id, token, revision, result):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            job=self.db.execute('SELECT j.*,l.token,l.lease_until FROM jobs j JOIN job_leases l ON l.job_id=j.id WHERE j.id=?',(id,)).fetchone()
            if not job or job['state']!='running' or job['token']!=token or job['lease_until']<time.time():raise ValueError('Stale job lease')
            if job['revision']!=revision:raise ValueError('Source revision changed')
            detail=json.dumps(result)
            if len(detail)>50000:raise ValueError('Job result too large')
            self.db.execute("UPDATE jobs SET state='done',detail=?,updated_at=? WHERE id=?",(detail,time.time(),id))
            self.db.execute('DELETE FROM job_leases WHERE job_id=?',(id,))
            self.change('job_done',id,job['kind']+': '+job['source_key'])
        return {'ok':True}

    def claim_reminder(self, now=None):
        now=time.time() if now is None else float(now)
        local=datetime.fromtimestamp(now,local_timezone())
        # Catch up when the laptop wakes during working hours; never create overnight noise.
        if local.weekday()>4 or not 9<=local.hour<18:return None
        day=local.date().isoformat()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute("UPDATE reminders SET state='uncertain' WHERE state='sending' AND created_at<?",(now-120,))
            if self.db.execute("SELECT COUNT(*) FROM reminders WHERE day=? AND state IN ('sending','sent','uncertain')",(day,)).fetchone()[0]>=3:return None
            rows=self.db.execute("SELECT id,due_at FROM obligations o WHERE state='open' AND due_at<=? AND NOT EXISTS(SELECT 1 FROM reminder_items i WHERE i.obligation_id=o.id AND i.due_at=o.due_at AND i.day=?) ORDER BY due_at",(now,day)).fetchall()
            rows=[r for r in rows if not self.is_legacy_obligation(r['id'])][:50]
            if not rows:return None
            rid=str(uuid.uuid4());payload=[dict(r) for r in rows]
            self.db.execute("INSERT INTO reminders VALUES (?,?,?,'sending',?,NULL)",(rid,day,json.dumps(payload),now))
            for row in rows:self.db.execute('INSERT INTO reminder_items VALUES (?,?,?,?)',(rid,row['id'],row['due_at'],day))
            return {'id':rid}

    def reminder_payload(self, id):
        with self.db:
            row=self.db.execute("SELECT * FROM reminders WHERE id=? AND state='sending'",(id,)).fetchone()
            if not row:return None
            active=self.db.execute("SELECT o.id FROM obligations o JOIN reminder_items i ON i.obligation_id=o.id WHERE i.reminder_id=? AND o.state='open' AND o.due_at=i.due_at",(id,)).fetchall()
            active=[r for r in active if not self.is_legacy_obligation(r['id'])]
            if not active:
                self.db.execute("UPDATE reminders SET state='cancelled',finished_at=? WHERE id=?",(time.time(),id));return None
            return {'count':len(active)}

    def finish_reminder(self, id, delivered):
        with self.db:
            state='sent' if delivered else 'uncertain'
            changed=self.db.execute("UPDATE reminders SET state=?,finished_at=? WHERE id=? AND state='sending'",(state,time.time(),id)).rowcount
            if changed:self.change('reminder',id,state)
        return {'ok':bool(changed)}

    def _work_ids(self, conversation_id):
        return [r[0] for r in self.db.execute('SELECT work_id FROM work_memberships WHERE conversation_id=? ORDER BY work_id',(conversation_id,))]

    def _obligation_detail(self, row):
        o=dict(row)
        o['work_ids']=self._work_ids(o['conversation_id'])
        return o

    def _work_detail(self, row):
        w=dict(row)
        # Read the editable title and its version in one SQLite snapshot. A
        # concurrent rename must never pair an old title with the new version.
        name=self.db.execute("SELECT w.title,n.value name_record FROM work w LEFT JOIN meta n ON n.key='work_name:' || w.id WHERE w.id=?",(w['id'],)).fetchone()
        w['title']=name['title']
        w['title_version']=json.loads(name['name_record'])['version'] if name['name_record'] else 0
        w=self.label_work_rows([w])[0]
        w['intent']=self.intent(w['id'])
        w['decisions']=[dict(r) for r in self.db.execute('SELECT d.id,d.conversation_id,d.text,d.quote,d.evidence_event event_id FROM decisions d JOIN work_memberships m ON m.conversation_id=d.conversation_id WHERE m.work_id=?',(w['id'],))]
        w['conversations']=[dict(r) for r in self.db.execute('SELECT c.id,c.source,c.external_id,c.title,c.url,c.model,c.updated_at,r.summary,r.next_action,r.updated_at checkpoint_at,e.analysis_state,e.occurred_at latest_occurred_at,CASE WHEN c.source="slack" THEN e.text ELSE NULL END preview_text FROM conversations c JOIN work_memberships m ON m.conversation_id=c.id LEFT JOIN return_points r ON r.conversation_id=c.id LEFT JOIN events e ON e.id=c.latest_event WHERE m.work_id=? ORDER BY c.updated_at DESC',(w['id'],))]
        for c in w['conversations']:
            c['preview']=slack_preview(c.pop('preview_text'))
            if c['source']=='slack':c['title']=slack_label(c['title'],c['preview'],c['external_id'])
            c['work_ids']=self._work_ids(c['id'])
            c['checkpoint']=self._record('checkpoint:'+c['id'],None)
            c['handoff']=self.handoff(c['id'])
            c['role']='tracking_archive' if self.is_tracking(c['source'],c['external_id']) else 'source'
        return w

    def associate(self, conversation_id, work_id, action):
        text(conversation_id,100);text(work_id,100)
        if action not in ('add','remove'):raise ValueError('Invalid association action')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            c=self.db.execute('SELECT work_id FROM conversations WHERE id=?',(conversation_id,)).fetchone()
            if not c:raise ValueError('Unknown conversation')
            if not self.db.execute('SELECT 1 FROM work WHERE id=?',(work_id,)).fetchone():raise ValueError('Unknown work')
            memberships=self._work_ids(conversation_id)
            if (action=='add' and work_id in memberships) or (action=='remove' and work_id not in memberships):return {'ok':True}
            primary=c['work_id']
            if action=='add':
                self.db.execute('INSERT INTO conversation_work_links VALUES (?,?)',(conversation_id,work_id))
            else:
                if len(memberships)==1:raise ValueError('Keep at least one work item associated. Add another before removing this one.')
                self.db.execute('DELETE FROM conversation_work_links WHERE conversation_id=? AND work_id=?',(conversation_id,work_id))
                if work_id==primary:
                    primary=next(w for w in memberships if w!=work_id)
                    self.db.execute('UPDATE conversations SET work_id=? WHERE id=?',(primary,conversation_id))
                    self.db.execute('DELETE FROM conversation_work_links WHERE conversation_id=? AND work_id=?',(conversation_id,primary))
                    self.db.execute('UPDATE obligations SET work_id=? WHERE conversation_id=?',(primary,conversation_id))
            # Explicit choices take precedence over automatic parent grouping and launch metadata.
            self.db.execute('INSERT INTO manual_links VALUES (?,?) ON CONFLICT(conversation_id) DO UPDATE SET work_id=excluded.work_id',(conversation_id,primary))
            self.db.execute('UPDATE work SET updated_at=? WHERE id=?',(time.time(),work_id))
            self.change('association',conversation_id,action+': '+work_id)
        return {'ok':True}

    def work_options(self, conversation_id, query=''):
        text(conversation_id,100);text(query,200)
        if not self.db.execute('SELECT 1 FROM conversations WHERE id=?',(conversation_id,)).fetchone():raise ValueError('Unknown conversation')
        linked=[dict(r) for r in self.db.execute('SELECT w.id,w.title,w.status FROM work w JOIN work_memberships m ON m.work_id=w.id WHERE m.conversation_id=? ORDER BY w.title',(conversation_id,))]
        # instr treats search text literally, including SQL wildcard characters.
        options=[dict(r) for r in self.db.execute("SELECT w.id,w.title,w.status FROM work w WHERE (instr(lower(w.title),lower(?))>0 OR EXISTS(SELECT 1 FROM work_memberships sm JOIN conversations sc ON sc.id=sm.conversation_id JOIN events se ON se.id=sc.latest_event WHERE sm.work_id=w.id AND sc.source='slack' AND instr(lower(se.text || ' ' || slack_search_text(se.text)),lower(?))>0)) AND NOT EXISTS(SELECT 1 FROM work_memberships m WHERE m.work_id=w.id AND m.conversation_id=?) AND EXISTS(SELECT 1 FROM work_memberships m WHERE m.work_id=w.id) ORDER BY w.updated_at DESC,w.id LIMIT 51",(query,query,conversation_id))]
        return {'linked':self.label_work_rows(linked),'options':self.label_work_rows(options[:50]),'has_more':len(options)>50}

    def thread_context(self, thread_id, work_id=None):
        if not isinstance(thread_id,str) or not thread_id.startswith('thr_') or len(thread_id)>100:
            raise ValueError('Invalid thread id')
        c=self.db.execute("SELECT id,work_id FROM conversations WHERE source='bb' AND external_id=?",(thread_id,)).fetchone()
        associated=[dict(r) for r in self.db.execute('SELECT w.id,w.title,w.status FROM work w JOIN work_memberships m ON m.work_id=w.id WHERE m.conversation_id=? ORDER BY w.title',(c['id'],))] if c else []
        # A removed selection falls back to this thread's current primary work.
        selected=work_id if work_id in {w['id'] for w in associated} else (c['work_id'] if c else None)
        row=self.db.execute('SELECT * FROM work WHERE id=?',(selected,)).fetchone() if selected else None
        return {'work':self._work_detail(row) if row else None,'associated_work':self.label_work_rows(associated),
                'obligations':[self._obligation_detail(r) for r in self.db.execute("SELECT o.* FROM obligations o JOIN work_memberships m ON m.conversation_id=o.conversation_id WHERE m.work_id=? AND o.state IN ('open','needs_review') ORDER BY o.due_at IS NULL,o.due_at,o.updated_at DESC",(row['id'],))] if row else [],
                'now':time.time()}

    def snapshot(self):
        now=time.time()
        sources=[dict(r) for r in self.db.execute('SELECT * FROM sources ORDER BY name')]
        for s in sources:
            s.pop('checkpoint',None)
            if s['state']=='current' and s['last_success'] and now-s['last_success']>900:s['state']='stale'
        work=[dict(r) for r in self.db.execute('''SELECT w.* FROM work w WHERE EXISTS(SELECT 1 FROM work_memberships m WHERE m.work_id=w.id)
              AND (w.focus=1 OR EXISTS(SELECT 1 FROM work_memberships m JOIN conversations c ON c.id=m.conversation_id WHERE m.work_id=w.id AND c.source='bb') OR EXISTS(SELECT 1 FROM work_memberships m JOIN obligations o ON o.conversation_id=m.conversation_id WHERE m.work_id=w.id AND o.state IN ('open','needs_review')) OR EXISTS(SELECT 1 FROM conversation_work_links l WHERE l.work_id=w.id))
              ORDER BY focus DESC,updated_at DESC LIMIT 200''')]
        work=[self._work_detail(w) for w in work]
        seen=self.db.execute("SELECT value FROM meta WHERE key='last_seen_seq'").fetchone()
        last_seen=int(seen[0]) if seen else 0
        attention=self.attention(now)
        return {'legacy':{k:v for k,v in self.legacy_page().items() if k!='items'},'attention':attention[:100],'attention_total':len(attention),'attention_unseen':sum(not a['seen'] for a in attention),'today':self.day_plan(),'previous_day':self.previous_day_plan(),'work':work,'obligations':[self._obligation_detail(r) for r in self.db.execute("SELECT * FROM obligations WHERE state IN ('open','needs_review') ORDER BY due_at IS NULL,due_at,updated_at DESC LIMIT 250")],
                'sources':sources,'changes':self.label_changes([dict(r) for r in self.db.execute("SELECT * FROM changes WHERE seq>? AND NOT EXISTS(SELECT 1 FROM meta WHERE key='change_seen:'||changes.seq) ORDER BY seq DESC LIMIT 100",(last_seen,))]),
                'analysis':dict(self.db.execute("SELECT SUM(analysis_state='pending') pending,SUM(analysis_state='running') running,SUM(analysis_error IS NOT NULL) errors FROM events").fetchone()),
                'analyzer':json.loads(self.db.execute("SELECT value FROM meta WHERE key='analyzer_last_result'").fetchone()[0]) if self.db.execute("SELECT 1 FROM meta WHERE key='analyzer_last_result'").fetchone() else None,
                'reminders':[dict(r) for r in self.db.execute('SELECT id,day,state,created_at,finished_at FROM reminders ORDER BY created_at DESC LIMIT 20')],
                'jobs':[dict(r) for r in self.db.execute('SELECT * FROM jobs ORDER BY updated_at DESC LIMIT 50')],
                'seq':self.db.execute('SELECT COALESCE(MAX(seq),0) FROM changes').fetchone()[0],'now':now}

    def acknowledge(self, seq):
        if not isinstance(seq,int) or seq<0:raise ValueError('Invalid sequence')
        with self.db:
            self.db.execute("INSERT INTO meta(key,value) VALUES ('last_seen_seq',?) ON CONFLICT(key) DO UPDATE SET value=CAST(MAX(CAST(value AS INTEGER),CAST(excluded.value AS INTEGER)) AS TEXT)",(str(seq),))
        return {'ok':True}

    def import_snapshot(self, payload):
        if payload.get('schema_version') not in (1,2,3):raise ValueError('Unsupported export version')
        names=['sources','conversations','work','events','obligations','changes','jobs','meta','decisions','manual_links','return_points','job_leases','reminders','reminder_items','conversation_work_links']
        extra=['source_references','context_requests','completion_reviews','completion_audit']
        tables=payload.get('tables')
        if payload.get('schema_version')==1 and isinstance(tables,dict):tables={**tables,'conversation_work_links':[]}
        if payload.get('schema_version') in (1,2) and isinstance(tables,dict):tables={**tables,**{name:[] for name in extra}}
        names+=extra
        if not isinstance(tables,dict) or set(tables)!=set(names):raise ValueError('Export tables do not match schema')
        if any(not isinstance(rows,list) or len(rows)>100000 for rows in tables.values()):raise ValueError('Invalid export size')
        work_ids={r['id'] for r in tables['work']};conversations={r['id'] for r in tables['conversations']};events={r['id'] for r in tables['events']}
        for r in tables['conversations']:
            if r['work_id'] not in work_ids or r['source'] not in SOURCES:raise ValueError('Broken work reference')
        for r in tables['conversation_work_links']:
            if r['work_id'] not in work_ids or r['conversation_id'] not in conversations:raise ValueError('Broken work association')
        for r in tables['events']:
            if r['conversation_id'] not in conversations:raise ValueError('Broken conversation reference')
        for r in tables['obligations']:
            if r['work_id'] not in work_ids or r['conversation_id'] not in conversations or r['evidence_event'] not in events:raise ValueError('Broken obligation evidence')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if any(self.db.execute('SELECT 1 FROM '+name+' LIMIT 1').fetchone() for name in names if name!='sources'):raise ValueError('Import requires an empty destination; existing work is never replaced')
            self.db.execute('DELETE FROM sources')
            for name in names:
                columns=[r[1] for r in self.db.execute('PRAGMA table_info('+name+')')]
                for row in tables[name]:
                    if not isinstance(row,dict) or set(row)!=set(columns):raise ValueError('Export row does not match schema')
                    self.db.execute('INSERT INTO '+name+' ('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',[row[c] for c in columns])
            # Carry unresolved work, but require explicit ownership reconciliation before dispatch.
            self.db.execute("UPDATE jobs SET state='held' WHERE state IN ('pending','running')")
            self.db.execute('DELETE FROM job_leases')
            self.db.execute("UPDATE context_requests SET state='pending',lease_token=NULL,lease_until=NULL WHERE state='running'")
            self.db.execute("UPDATE completion_reviews SET state='queued',lease_token=NULL,lease_until=NULL WHERE state='running'")
            for row in self.db.execute('SELECT id FROM conversations').fetchall():self.index_references(row[0])
            self.db.execute("UPDATE events SET analysis_state='pending',lease_token=NULL,lease_until=NULL WHERE analysis_state='running'")
            self.db.execute("UPDATE reminders SET state='uncertain' WHERE state='sending'")
            self.change('import','ledger','Imported commitments and return points; unfinished agent jobs held for ownership reconciliation')
        return {'ok':True,'held_jobs':self.db.execute("SELECT count(*) FROM jobs WHERE state='held'").fetchone()[0]}

    def export(self):
        return {'schema_version':3,'exported_at':time.time(), 'tables':{name:[dict(r) for r in self.db.execute('SELECT * FROM '+name)] for name in ['sources','conversations','work','events','obligations','changes','jobs','meta','decisions','manual_links','return_points','job_leases','reminders','reminder_items','conversation_work_links','source_references','context_requests','completion_reviews','completion_audit']}}
