"""Explicitly retired tracking mirrors, with lossless commitment reconciliation."""
import re
import time

class TrackingMixin:
    def tracking_teams(self):
        return self._record('tracking_teams',[])

    def is_tracking(self, source, external_id):
        return source=='linear' and external_id.rpartition('-')[0] in self.tracking_teams()

    def configure_tracking(self, teams):
        if not isinstance(teams,list) or len(teams)>50 or any(not isinstance(t,str) or not re.fullmatch('[A-Z][A-Z0-9]{0,19}',t) for t in teams):
            raise ValueError('Expected Linear team keys')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self._put_record('tracking_teams',sorted(set(teams)))
            for c in self.db.execute("SELECT id,source,external_id FROM conversations WHERE source='linear'").fetchall():
                if self.is_tracking(c['source'],c['external_id']):
                    self.db.execute("UPDATE events SET analysis_state='reference_only',lease_token=NULL WHERE conversation_id=? AND analysis_state IN ('pending','running')",(c['id'],))
                else:
                    self.db.execute("UPDATE events SET analysis_state='pending' WHERE id=(SELECT latest_event FROM conversations WHERE id=?) AND analysis_state='reference_only'",(c['id'],))
        return {'ok':True,'teams':self.tracking_teams()}

    def is_legacy_obligation(self, obligation_id):
        c=self.db.execute('SELECT c.source,c.external_id FROM conversations c JOIN obligations o ON o.conversation_id=c.id WHERE o.id=?',(obligation_id,)).fetchone()
        return bool(c and self.is_tracking(c['source'],c['external_id']) and not self._record('tracking_keep:'+obligation_id,False))

    def legacy_page(self, offset=0):
        if type(offset)!=int or not 0<=offset<=100000:raise ValueError('Invalid offset')
        rows=[];now=time.time()
        for row in self.db.execute("SELECT o.*,c.title source_title,c.url,c.external_id FROM obligations o JOIN conversations c ON c.id=o.conversation_id WHERE c.source='linear' AND o.state IN ('open','needs_review') ORDER BY o.due_at IS NULL,o.due_at,o.updated_at DESC"):
            if self.is_legacy_obligation(row['id']):rows.append({**dict(row),'work_ids':self._work_ids(row['conversation_id'])})
        return {'items':rows[offset:offset+50],'total':len(rows),'due':sum(r['due_at'] is not None and r['due_at']<=now for r in rows),'has_more':offset+50<len(rows),'teams':self.tracking_teams()}

    def keep_legacy(self, obligation_id, version):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute('SELECT version,state FROM obligations WHERE id=?',(obligation_id,)).fetchone()
            if not row or row['version']!=version or row['state'] not in ('open','needs_review'):raise ValueError('Commitment changed; reload before keeping it')
            if not self.is_legacy_obligation(obligation_id):raise ValueError('Not pending legacy reconciliation')
            self._put_record('tracking_keep:'+obligation_id,True)
            self.change('legacy_reconciled',obligation_id,'Retained legacy commitment for active follow-up')
        return {'ok':True}
