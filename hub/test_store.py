from test_support import isolate_storage
import tempfile
import time
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from store import Store, digest

class LedgerTests(unittest.TestCase):
    def setUp(self):
        isolate_storage(self)

        self.temp=tempfile.TemporaryDirectory(); self.path=Path(self.temp.name)/'work.db';self.s=Store(self.path)
    def tearDown(self):self.s.close();self.temp.cleanup()
    def ingest(self, version='1', body='I will send the draft tomorrow.', **extra):
        return self.s.ingest('bb',[dict(external_id='a',title='Draft',version=version,text=body,**extra)],checkpoint=version)
    def analysis(self, claim, **extra):
        return self.s.apply_analysis(claim['id'],claim['lease_token'],dict(summary='Draft in progress',next_action='Send draft',decisions=[],obligations=[],**extra))
    def obligation(self, **extra):
        return dict(title='Send draft',owner='Alex',direction='i_owe',due_at=None,state='open',quote='I will send the draft tomorrow.',confidence=.99,explicit_resolution=False,**extra)
    def test_duplicate_and_atomic_checkpoint(self):
        self.assertEqual(self.ingest()['inserted'],1);self.assertEqual(self.ingest()['inserted'],0)
        with self.assertRaises(ValueError):self.s.ingest('bb',[dict(external_id='b',title='ok'),dict(external_id='c',title='bad',url='javascript:bad')],checkpoint='bad')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM conversations').fetchone()[0],1)
        self.assertEqual(self.s.db.execute("SELECT checkpoint FROM sources WHERE name='bb'").fetchone()[0],'"1"')
    def test_old_source_cannot_replace_new(self):
        self.ingest('new',occurred_at=time.time());eid=self.s.claim_analysis()['id']
        self.ingest('old',occurred_at=time.time()-100)
        self.assertEqual(self.s.db.execute('SELECT latest_event FROM conversations').fetchone()[0],eid)
    def test_stale_model_result_rejected(self):
        self.ingest();c=self.s.claim_analysis();self.ingest('2')
        with self.assertRaises(ValueError):self.analysis(c)
    def test_restart_recovers_lease_old_worker_cannot_write(self):
        self.ingest();c=self.s.claim_analysis();self.s.db.execute('UPDATE events SET lease_until=0');self.s.db.commit();self.s.close();self.s=Store(self.path)
        newer=self.s.claim_analysis();self.assertNotEqual(c['lease_token'],newer['lease_token'])
        with self.assertRaises(ValueError):self.analysis(c)
        self.assertTrue(self.analysis(newer)['applied'])
    def test_bad_evidence_rolls_back_return_point(self):
        self.ingest();c=self.s.claim_analysis();o=self.obligation();o['quote']='invented'
        with self.assertRaises(ValueError):self.s.apply_analysis(c['id'],c['lease_token'],dict(summary='bad',next_action='bad',obligations=[o]))
        self.assertEqual(self.s.snapshot()['work'][0]['summary'],'')
    def test_fulfillment_cancels_due_and_replay_does_not_reopen(self):
        self.ingest();c=self.s.claim_analysis();self.s.apply_analysis(c['id'],c['lease_token'],dict(summary='draft',next_action='send',obligations=[self.obligation()]))
        oid=self.s.snapshot()['obligations'][0]['id'];self.ingest('2','I sent the draft.');c=self.s.claim_analysis();o=self.obligation(existing_id=oid);o.update(state='fulfilled',quote='I sent the draft.',explicit_resolution=True)
        self.s.apply_analysis(c['id'],c['lease_token'],dict(summary='sent',next_action='',obligations=[o]));self.assertEqual(self.s.snapshot()['obligations'],[])
        self.ingest('3');c=self.s.claim_analysis();self.s.apply_analysis(c['id'],c['lease_token'],dict(summary='sent',next_action='',obligations=[self.obligation()]));self.assertEqual(self.s.snapshot()['obligations'],[])
    def test_decisions_survive_side_conversation(self):
        self.ingest(body='Use local polling.');c=self.s.claim_analysis();self.s.apply_analysis(c['id'],c['lease_token'],dict(summary='local',next_action='build',decisions=[dict(text='Local polling',quote='Use local polling.')]))
        self.s.ingest('bb',[dict(external_id='child',parent_id='a',title='Side',text='Working on UI')]);c=self.s.claim_analysis();self.analysis(c)
        w=self.s.snapshot()['work'][0];self.assertEqual(len(w['decisions']),1);self.assertEqual(len(w['conversations']),2)
    def test_focus_returns_only_bb_threads_from_all_memberships(self):
        self.s.ingest('bb',[dict(external_id='thr_root',title='Primary'),dict(external_id='thr_shared',title='Shared'),dict(external_id='thr_other',title='Other')])
        self.s.ingest('slack',[dict(external_id='source',title='Source')])
        work=digest('bb','thr_root')
        self.s.associate(digest('bb','thr_shared'),work,'add')
        self.s.associate(digest('slack','source'),work,'add')
        self.assertEqual(self.s.mutate('work',work,'focus')['thread_ids'],['thr_root','thr_shared'])
        self.assertNotIn('thread_ids',self.s.mutate('work',work,'unfocus'))
        self.assertEqual(self.s.mutate('work',digest('slack','source'),'focus')['thread_ids'],[])

    def test_remove_focus_preserves_work_followups_and_other_focus(self):
        self.ingest();c=self.s.claim_analysis()
        self.s.apply_analysis(c['id'],c['lease_token'],dict(summary='Draft in progress',next_action='Send draft',obligations=[self.obligation()]))
        work_id=digest('bb','a')
        def work(id):return next(w for w in self.s.snapshot()['work'] if w['id']==id)
        self.s.mutate('work',work_id,'focus')
        self.s.mutate('work',work_id,'waiting')
        before={'work': work(work_id), 'obligations': self.s.snapshot()['obligations']}
        self.s.mutate('work',work_id,'unfocus')
        self.s.mutate('work',work_id,'unfocus')
        after={'work': work(work_id), 'obligations': self.s.snapshot()['obligations']}
        self.assertEqual(after['work']['focus'],0)
        before['work']['focus']=0
        self.assertEqual(after,before)
        self.s.ingest('bb',[dict(external_id='b',title='Other')])
        other_id=digest('bb','b')
        self.s.mutate('work',other_id,'focus')
        self.s.mutate('work',work_id,'unfocus')
        self.assertEqual(work(other_id)['focus'],1)
        self.s.mutate('work',work_id,'focus')
        self.assertEqual(work(work_id)['focus'],1)
        self.assertEqual(work(other_id)['focus'],1)
        self.s.mutate('work',work_id,'unfocus')
        self.assertEqual(work(other_id)['focus'],1)
        self.s.close();self.s=Store(self.path)
        self.assertEqual(work(other_id)['focus'],1)

    def test_manual_link_and_version_conflict(self):
        self.ingest();c=self.s.claim_analysis();self.s.apply_analysis(c['id'],c['lease_token'],dict(summary='',next_action='',obligations=[self.obligation()]))
        o=self.s.snapshot()['obligations'][0];self.s.mutate('obligation',o['id'],'fulfilled',version=o['version'])
        with self.assertRaises(ValueError):self.s.mutate('obligation',o['id'],'open',version=o['version'])
        self.s.ingest('bb',[dict(external_id='b',title='Other')]);self.s.mutate('conversation',digest('bb','a'),'link',value=digest('bb','b'));self.ingest('2')
        self.assertEqual(self.s.db.execute('SELECT work_id FROM conversations WHERE id=?',(digest('bb','a'),)).fetchone()[0],digest('bb','b'))

if __name__=='__main__':unittest.main()

class JobTests(unittest.TestCase):
    setUp=LedgerTests.setUp
    tearDown=LedgerTests.tearDown
    def test_revision_dedup(self):
        a=self.s.observe_job('fixture_review','repo#1','head1');b=self.s.observe_job('fixture_review','repo#1','head1');self.assertTrue(a['inserted']);self.assertFalse(b['inserted'])
        j=self.s.claim_job('fixture_review');self.s.finish_job(j['id'],j['lease_token'],'head1',{'summary':'ok'});self.assertIsNone(self.s.claim_job('fixture_review'))
    def test_new_revision_rejects_old_review(self):
        self.s.observe_job('fixture_review','repo#1','head1');j=self.s.claim_job('fixture_review');self.s.observe_job('fixture_review','repo#1','head2')
        with self.assertRaises(ValueError):self.s.finish_job(j['id'],j['lease_token'],'head1',{})
        self.assertEqual(self.s.claim_job('fixture_review')['revision'],'head2')
    def test_restart_reclaims_job_but_invalidates_old_token(self):
        self.s.observe_job('fixture_review','repo#1','head');j=self.s.claim_job('fixture_review');self.s.db.execute('UPDATE job_leases SET lease_until=0');self.s.db.commit();self.s.close();self.s=Store(self.path)
        fresh=self.s.claim_job('fixture_review')
        with self.assertRaises(ValueError):self.s.finish_job(j['id'],j['lease_token'],'head',{})
        self.assertTrue(self.s.finish_job(fresh['id'],fresh['lease_token'],'head',{})['ok'])

class ReminderTests(unittest.TestCase):
    setUp=LedgerTests.setUp
    tearDown=LedgerTests.tearDown
    def due(self):
        # Monday 10AM in the user's timezone.
        now=datetime(2026,11,16,10,tzinfo=ZoneInfo('UTC')).timestamp()
        with self.s.db:self.s.db.execute("INSERT INTO obligations(id,work_id,conversation_id,evidence_event,evidence_quote,title,owner,direction,due_at,state,updated_at) VALUES ('due','w','c','e','quote','Send draft','Alex','i_owe',?,'open',?)",(now-60,now))
        return now
    def test_fulfillment_cancels_queued_reminder(self):
        now=self.due();r=self.s.claim_reminder(now);self.s.mutate('obligation','due','fulfilled',version=1);self.assertIsNone(self.s.reminder_payload(r['id']))
        self.assertIsNone(self.s.claim_reminder(now+60))
    def test_restart_never_blindly_resends_uncertain_notification(self):
        now=self.due();r=self.s.claim_reminder(now);self.s.close();self.s=Store(self.path)
        self.assertIsNone(self.s.claim_reminder(now+300));self.assertEqual(self.s.db.execute('SELECT state FROM reminders').fetchone()[0],'uncertain')
    def test_sent_deduplicated_and_quiet_hours_respected(self):
        now=self.due();self.assertIsNone(self.s.claim_reminder(now-12*3600));r=self.s.claim_reminder(now);self.assertEqual(self.s.reminder_payload(r['id'])['count'],1);self.s.finish_reminder(r['id'],True);self.assertIsNone(self.s.claim_reminder(now+60))

class PortabilityTests(unittest.TestCase):
    setUp=LedgerTests.setUp
    tearDown=LedgerTests.tearDown
    def test_import_preserves_work_and_holds_unfinished_jobs(self):
        self.s.ingest('bb',[{'external_id':'main','title':'Work','text':'I will send the draft.'}]);c=self.s.claim_analysis();self.s.apply_analysis(c['id'],c['lease_token'],{'summary':'Draft started','next_action':'Send it','obligations':[dict(title='Send draft',owner='Alex',direction='i_owe',due_at=None,state='open',quote='I will send the draft.',confidence=.99)]})
        self.s.observe_job('fixture_review','repo#1','head');self.s.claim_job('fixture_review')
        other=Store(Path(self.temp.name)/'imported.db')
        try:
            self.assertEqual(other.import_snapshot(self.s.export())['held_jobs'],1)
            self.assertEqual(other.snapshot()['work'][0]['next_action'],'Send it');self.assertEqual(len(other.snapshot()['obligations']),1)
            self.assertIsNone(other.claim_job('fixture_review'))
            with self.assertRaises(ValueError):other.import_snapshot(self.s.export())
        finally:other.close()
