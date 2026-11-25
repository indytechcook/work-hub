"""Portable UI intent and attention, independent of BB and of model extraction.

Versioned intent, plans and acknowledgements live in exported meta records. No
new background dispatcher or notification channel is installed by this module.
"""
import json
import time
from datetime import datetime
from settings import timezone as local_timezone
import hashlib
import unicodedata
from routing import OB_IN_WORK


def fingerprint(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:32]


def work_name(title):
    if not isinstance(title,str) or any(unicodedata.category(c) in ('Cc','Zl','Zp') for c in title):
        raise ValueError('Use a single-line work name without control characters')
    title=title.strip()
    if not 1<=len(title)<=200:raise ValueError('Work name must be 1–200 characters')
    return title


class PresentationMixin:
    def _record(self, key, default):
        row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _put_record(self, key, value):
        self.db.execute('INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                        (key, json.dumps(value)))

    def intent(self, work_id):
        return self._record('intent:'+work_id, dict(next_action='', purpose='', project='', thread_id=None, version=0, updated_at=None))

    def create_work(self, title, purpose='', request_id=None):
        title = work_name(title)
        if not isinstance(purpose, str) or len(purpose) > 3000:
            raise ValueError('Purpose must be at most 3000 characters')
        if request_id is not None and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 100):
            raise ValueError('Invalid creation request ID')
        purpose = purpose.strip()
        payload = fingerprint(title, purpose)
        wid = fingerprint('standalone work', request_id or payload)
        key = 'standalone:' + wid
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            prior = self._record(key, None)
            if prior is not None:
                if prior != payload:
                    raise ValueError('This creation request already saved different work')
                return dict(id=wid, created=False)
            now = time.time()
            self.db.execute('INSERT INTO work(id,title,updated_at) VALUES (?,?,?)', (wid, title, now))
            self._put_record(key, payload)
            self._put_record('work_name:' + wid, dict(version=1, updated_at=now))
            if purpose:
                intent = self.intent(wid)
                intent.update(purpose=purpose, version=1, updated_at=now)
                self._put_record('intent:' + wid, intent)
            self.change('work_created', wid, title)
        return dict(id=wid, created=True)

    def rename_work(self, work_id, version, title):
        title=work_name(title)
        if type(version) is not int or version<0:raise ValueError('Invalid name version')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if not self.db.execute('SELECT 1 FROM work WHERE id=?',(work_id,)).fetchone():raise ValueError('Unknown work')
            current=self._record('work_name:'+work_id,dict(version=0))
            if current['version']!=version:raise ValueError('This work name changed in another view. Reload the saved name before saving.')
            now=time.time()
            self.db.execute('UPDATE work SET title=?,updated_at=? WHERE id=?',(title,now,work_id))
            # An explicit marker distinguishes even a user name beginning with Slack
            # from an automatic source label. Export/import already includes meta.
            self._put_record('work_name:'+work_id,dict(version=version+1,updated_at=now))
            self.change('work_renamed',work_id,title)
        return dict(title=title,title_version=version+1)

    def record_thread_name(self, work_id, thread_id, title):
        """Mirror a confirmed BB rename without collecting or rewriting evidence."""
        title=work_name(title)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute("SELECT c.id FROM conversations c JOIN work_memberships m ON m.conversation_id=c.id WHERE c.source='bb' AND c.external_id=? AND m.work_id=?",(thread_id,work_id)).fetchone()
            if not row:raise ValueError('Thread is no longer associated with this work')
            self.db.execute('UPDATE conversations SET title=? WHERE id=?',(title,row['id']))
            self.change('thread_renamed',row['id'],title)
        return {'ok':True}

    def save_intent(self, work_id, version, next_action, purpose='', project='', thread_id=None):
        for value, limit in [(next_action,1500),(purpose,3000),(project,120)]:
            if not isinstance(value,str) or len(value)>limit:raise ValueError('Invalid intent text')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if not self.db.execute('SELECT 1 FROM work WHERE id=?',(work_id,)).fetchone():raise ValueError('Unknown work')
            if self.intent(work_id)['version']!=version:raise ValueError('Your chosen context changed in another view. Reload it before saving.')
            if thread_id is not None and not self.db.execute("SELECT 1 FROM conversations c JOIN work_memberships m ON m.conversation_id=c.id WHERE c.source='bb' AND c.external_id=? AND m.work_id=?",(thread_id,work_id)).fetchone():
                raise ValueError('Resume conversation is not associated with this work')
            value=dict(next_action=next_action.strip(),purpose=purpose.strip(),project=project.strip(),thread_id=thread_id,version=version+1,updated_at=time.time())
            self._put_record('intent:'+work_id,value)
            self.change('chosen_context',work_id,'Chosen next action updated')
        return value

    def day_plan(self, date=None):
        date=date or datetime.now(local_timezone()).date().isoformat()
        try:
            if datetime.strptime(date,'%Y-%m-%d').date().isoformat()!=date:raise ValueError()
        except (ValueError,TypeError):raise ValueError('Expected a local date YYYY-MM-DD')
        return self._record('day_plan:'+date,dict(date=date,work_ids=[],note='',version=0,updated_at=None))

    def acknowledge_changes(self, seqs):
        if not isinstance(seqs,list) or len(seqs)>100 or any(type(seq) is not int or seq<1 for seq in seqs):raise ValueError('Choose up to 100 displayed changes')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            for seq in set(seqs):
                if self.db.execute('SELECT 1 FROM changes WHERE seq=?',(seq,)).fetchone():
                    self._put_record('change_seen:'+str(seq),dict(at=time.time()))
        return {'ok':True}

    def previous_day_plan(self):
        today=self.day_plan()['date']
        row=self.db.execute("SELECT value FROM meta WHERE key LIKE 'day_plan:%' AND key<? ORDER BY key DESC LIMIT 1",('day_plan:'+today,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_day_plan(self, date, version, work_ids, note=''):
        if not isinstance(work_ids,list) or len(work_ids)>12 or any(not isinstance(i,str) for i in work_ids) or len(set(work_ids))!=len(work_ids):raise ValueError('Choose up to 12 distinct work items')
        if not isinstance(note,str) or len(note)>3000:raise ValueError('Invalid day note')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.day_plan(date)['version']!=version:raise ValueError('This day plan changed. Reload before saving.')
            for wid in work_ids:
                if not self.db.execute('SELECT 1 FROM work WHERE id=?',(wid,)).fetchone():raise ValueError('Unknown planned work')
            plan=dict(date=date,work_ids=work_ids,note=note.strip(),version=version+1,updated_at=time.time())
            self._put_record('day_plan:'+date,plan)
            self.change('day_plan',date,'Day plan saved')
        return plan

    def attention(self, now=None):
        now=time.time() if now is None else now
        local=datetime.fromtimestamp(now,local_timezone())
        end=local.replace(hour=23,minute=59,second=59,microsecond=999999).timestamp()
        rows=self.db.execute("SELECT o.*,c.source,e.occurred_at,e.received_at FROM obligations o JOIN conversations c ON c.id=o.conversation_id LEFT JOIN events e ON e.id=o.evidence_event WHERE o.state IN ('open','needs_review')").fetchall()
        result=[]
        for row in rows:
            o=dict(row);reason=None;rank=4
            if self.is_legacy_obligation(o['id']):continue
            if o['state']=='needs_review':reason='Confirm this captured commitment';rank=1
            elif o['due_at'] is not None and o['due_at']<=now:reason='Overdue commitment';rank=0
            elif o['due_at'] is not None and o['due_at']<=end:reason='Commitment due today';rank=2
            elif o['direction']=='i_owe':reason='An open commitment needs your follow-through';rank=3
            elif self.db.execute("SELECT 1 FROM work w JOIN work_memberships m ON m.work_id=w.id WHERE m.conversation_id=? AND w.status='done'",(o['conversation_id'],)).fetchone():reason='Work is done; this commitment is still open';rank=1
            if not reason:continue
            # Extraction can increment version without a meaningful change. Never
            # re-alert merely because a collector/model revisited the same promise.
            token=fingerprint('obligation',o['id'],o['title'],o['owner'],o['direction'],o['state'],o['due_at'],reason)
            result.append(dict(token=token,kind='obligation',entity_id=o['id'],title=o['title'],reason=reason,rank=rank,
                work_ids=self._work_ids(o['conversation_id']),source=o['source'],at=o['updated_at'],occurred_at=o['occurred_at'],received_at=o['received_at'],
                seen=self._record('attention_seen:'+token,None) is not None))
        for c in self.db.execute("SELECT id,title FROM conversations WHERE source='bb'"):
            h=self.handoff(c['id'])
            if not h or h['status'] not in ('blocked','needs_input','failed','ready_for_review'):continue
            token=fingerprint('handoff',c['id'],h['turn_at'],h['status'],h['next_action'],h['blocker'])
            result.append(dict(token=token,kind='handoff',entity_id=c['id'],title=c['title'],reason=h['blocker'] or ('Agent report ready for review' if h['status']=='ready_for_review' else 'Agent needs your attention'),rank=1,work_ids=self._work_ids(c['id']),source='bb',at=h['at'],occurred_at=h['at'],received_at=h['at'],seen=self._record('attention_seen:'+token,None) is not None))
        for row in self.db.execute("SELECT * FROM jobs WHERE state IN ('failed','error','uncertain','held')"):
            j=dict(row);token=fingerprint('job',j['id'],j['state'])
            result.append(dict(token=token,kind='job',entity_id=j['id'],title=j['source_key'],reason='Agent job '+j['state']+'; inspect before retrying',rank=0,work_ids=[],source=j['kind'],at=j['updated_at'],occurred_at=None,received_at=None,seen=self._record('attention_seen:'+token,None) is not None))
        for name,health in self._record('source_health',{}).items():
            if health['active']:
                token=fingerprint('health',name,health['since'])
                result.append(dict(token=token,kind='health',entity_id=name,title='Check '+name+' coverage',
                    reason=health['detail'],rank=0,work_ids=[],source=name,at=health['since'],occurred_at=None,received_at=None,
                    seen=self._record('attention_seen:'+token,None) is not None))
        for row in self.db.execute("""SELECT m.value,c.title FROM meta m JOIN conversations c ON c.id=json_extract(m.value,'$.cid')
                WHERE m.key LIKE 'reply_watch:%' AND json_extract(m.value,'$.reply') IS NOT NULL"""):
            watch=json.loads(row['value'])
            if watch.get('obligation') and not self.db.execute("SELECT 1 FROM obligations WHERE id=? AND state='open'",(watch['obligation'],)).fetchone():continue
            token=fingerprint('reply',watch['cid'],watch['reply'])
            if any(a['token']==token for a in result):continue
            work_ids=self._work_ids(watch['cid'])
            if watch.get('work') and watch['work'] not in work_ids:work_ids.append(watch['work'])
            result.append(dict(token=token,kind='reply',entity_id=watch['cid'],title='Reply received · '+row['title'],
                reason='Open the watched conversation to check the answer.',rank=0,work_ids=work_ids,source=watch['source'],
                at=watch['checked'],occurred_at=watch['at'],received_at=watch['checked'],seen=self._record('attention_seen:'+token,None) is not None))
        result.sort(key=lambda a:(a['seen'],a['rank'],-a['at'],a['token']))
        return result

    def acknowledge_attention(self, tokens):
        if not isinstance(tokens,list) or len(tokens)>100 or any(not isinstance(t,str) or len(t)!=32 for t in tokens):raise ValueError('Invalid attention selection')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            current={a['token'] for a in self.attention()}
            for token in tokens:
                if token in current:self._put_record('attention_seen:'+token,time.time())
        return {'ok':True}

    def work_detail(self, work_id):
        row=self.db.execute('SELECT * FROM work WHERE id=?',(work_id,)).fetchone()
        if not row:raise ValueError('This work no longer exists')
        return dict(work=self._work_detail(row),obligations=[self._obligation_detail(r) for r in self.db.execute("SELECT o.* FROM obligations o JOIN work_memberships m ON m.conversation_id=o.conversation_id WHERE m.work_id=? AND "+OB_IN_WORK+" ORDER BY o.state IN ('open','needs_review') DESC,o.due_at IS NULL,o.due_at,o.updated_at DESC",(work_id,))],now=time.time())

    def work_index(self, query='', status='all', offset=0):
        if not isinstance(query,str) or len(query)>200 or status not in ('all','active','waiting','done') or type(offset)!=int or not 0<=offset<=100000:raise ValueError('Invalid work search')
        where="""(EXISTS(SELECT 1 FROM work_memberships m WHERE m.work_id=w.id) OR EXISTS(SELECT 1 FROM meta WHERE key='standalone:'||w.id))
          AND (?='all' OR w.status=?) AND (instr(lower(w.title),lower(?))>0 OR instr(lower(COALESCE(g.names,'')),lower(?))>0
          OR EXISTS(SELECT 1 FROM conversations c JOIN work_memberships m ON m.conversation_id=c.id WHERE m.work_id=w.id AND instr(lower(c.title),lower(?))>0)
          OR EXISTS(SELECT 1 FROM obligations o JOIN work_memberships m ON m.conversation_id=o.conversation_id WHERE m.work_id=w.id AND instr(lower(o.title),lower(?))>0 AND """+OB_IN_WORK+""")
          OR EXISTS(SELECT 1 FROM meta WHERE key='intent:'||w.id AND instr(lower(value),lower(?))>0)
          OR EXISTS(SELECT 1 FROM work_memberships sm JOIN conversations sc ON sc.id=sm.conversation_id JOIN events se ON se.id=sc.latest_event WHERE sm.work_id=w.id AND sc.source='slack' AND instr(lower(se.text || ' ' || slack_search_text(se.text)),lower(?))>0))"""
        args=(status,status,query,query,query,query,query,query)
        groups=""" FROM work w LEFT JOIN (
          SELECT work_id,group_concat(name,' · ') names FROM
          (SELECT * FROM work_project_names ORDER BY name COLLATE NOCASE,name) GROUP BY work_id
        ) g ON g.work_id=w.id LEFT JOIN meta i ON i.key='intent:'||w.id WHERE """
        project="COALESCE(g.names,json_extract(i.value,'$.project'),'')"
        total=self.db.execute('SELECT COUNT(*)'+groups+where,args).fetchone()[0]
        rows=self.db.execute('SELECT w.*,'+project+' area'+groups+where+" ORDER BY area='',area COLLATE NOCASE,area,w.focus DESC,w.updated_at DESC,w.id LIMIT 50 OFFSET ?",(*args,offset)).fetchall()
        items=[]
        for row in rows:
            w=dict(row);intent=self.intent(w['id'])
            count=self.db.execute("SELECT COUNT(*) FROM obligations o JOIN work_memberships m ON m.conversation_id=o.conversation_id WHERE m.work_id=? AND o.state IN ('open','needs_review') AND "+OB_IN_WORK,(w['id'],)).fetchone()[0]
            items.append(dict(id=w['id'],title=w['title'],status=w['status'],focus=w['focus'],project=w['area'],next_action=intent['next_action'] or w['next_action'],open_count=count))
        return dict(items=self.label_work_rows(items),total=total,has_more=offset+len(items)<total)
