"""Immediate, agent-reported return points. Never proof of commitment completion."""
import json
import math
import time

class HandoffMixin:
    def handoff(self, conversation_id):
        return self._record('handoff:'+conversation_id,None)

    def context_handoffs(self, work, thread_id):
        if not work:return []
        conversations=sorted((c for c in work['conversations'] if c.get('handoff')),key=lambda c:(c['external_id']!=thread_id,-c['handoff']['at']))[:3]
        result=[]
        for c in conversations:
            h=c['handoff']
            result.append(dict(thread_id=c['external_id'],status=h['status'],origin=h['origin'],at=h['at'],progress=h['progress'][:1200],remaining=h['remaining'][:500],next_action=h['next_action'][:500],blocker=h['blocker'][:500],evidence=[e[:300] for e in h['evidence'][:3]]))
        return result

    def _save_handoff(self, cid, status, progress, remaining, next_action, blocker, evidence, at, turn_at, origin):
        old=self.handoff(cid)
        if old and (old['at']>at or old['turn_at']>turn_at):return old
        data=dict(status=status,progress=progress,remaining=remaining,next_action=next_action,blocker=blocker,evidence=evidence,at=at,turn_at=turn_at,origin=origin)
        if old and old['turn_at']==turn_at and origin=='capture' and old['origin']=='agent':
            # A final-output capture enriches provenance without replacing a structured report.
            if status!='failed':return old
        comparable=lambda d:{k:v for k,v in d.items() if k not in ('at','history')}
        if old and comparable(old)==comparable(data):return old
        history=([ {k:v for k,v in old.items() if k!='history'} ]+old.get('history',[]))[:5] if old else []
        data['history']=history
        self._put_record('handoff:'+cid,data)
        self.change('return_point',cid,'Agent return point saved; completion is unverified')
        return data

    def capture_handoff(self, cid, body, at):
        try:payload=json.loads(body)
        except (ValueError,TypeError):return
        if not isinstance(payload,dict) or not isinstance(payload.get('prompts'),list):return
        raw=payload.get('status');report=payload.get('assistant','')
        status={'idle':'paused','error':'failed','active':'active','starting':'active','pending':'needs_input'}.get(raw)
        if not status or not isinstance(report,str):return
        prompts=[p.get('at',0)/1000 for p in payload['prompts'] if isinstance(p,dict) and type(p.get('at')) in (int,float)]
        turn_at=max(prompts,default=0)
        if status=='paused' and not report:return
        if status=='active' and not self.handoff(cid):return
        self._save_handoff(cid,status,report[:6000] if status=='paused' else '', '', '',
            'Inspect the failed BB turn.' if status=='failed' else 'A BB interaction needs your response.' if status=='needs_input' else '',[],at,turn_at,'capture')

    def save_handoff(self, thread_id, status, progress, remaining='', next_action='', blocker='', evidence=None, turn_at=0):
        if status not in ('paused','blocked','needs_input','ready_for_review'):raise ValueError('Invalid return point status')
        for value,limit in [(progress,6000),(remaining,3000),(next_action,1500),(blocker,2000)]:
            if not isinstance(value,str) or len(value)>limit:raise ValueError('Invalid return point text')
        evidence=[] if evidence is None else evidence
        if not isinstance(evidence,list) or len(evidence)>15 or any(not isinstance(e,str) or len(e)>2000 for e in evidence):raise ValueError('Invalid evidence links')
        now=time.time()
        if type(turn_at) not in (int,float) or not math.isfinite(turn_at) or not 0<=turn_at<=now+60:raise ValueError('Invalid turn time')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            c=self.db.execute("SELECT id FROM conversations WHERE source='bb' AND external_id=?",(thread_id,)).fetchone()
            if not c:raise ValueError('Capture and associate this thread before saving a return point')
            return self._save_handoff(c['id'],status,progress,remaining,next_action,blocker,evidence,now,turn_at,'agent')
