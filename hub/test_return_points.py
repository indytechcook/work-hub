import json,tempfile,time,unittest
from pathlib import Path
from store import Store,digest

class ReturnPointTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name)/'work.db');self.now=time.time();self.cid=digest('bb','thr_demo')
    def tearDown(self):self.s.close();self.tmp.cleanup()
    def capture(self,status='idle',at=None,turn=None,report='Tests passed; deployment remains.'):
        self.s.ingest('bb',[dict(external_id='thr_demo',title='Release',text=json.dumps(dict(status=status,prompts=[dict(at=(turn or self.now-10)*1000,text='Work')],assistant=report)),occurred_at=at or self.now)])
    def test_capture_is_immediate_idempotent_and_does_not_resolve(self):
        self.capture();h=self.s.handoff(self.cid)
        self.assertEqual(h['origin'],'capture');self.assertEqual(h['status'],'paused')
        self.assertEqual(self.s.attention(),[])
        self.capture();self.assertEqual(self.s.handoff(self.cid),h)
        self.assertEqual(self.s.work_detail(self.cid)['work']['status'],'active')
        self.assertEqual(self.s.db.execute('SELECT analysis_state FROM events').fetchone()[0],'pending')
        self.s.close();self.s=Store(Path(self.tmp.name)/'work.db')
        self.assertEqual(self.s.handoff(self.cid),h)
    def test_explicit_checkpoint_survives_idle_and_chosen_intent(self):
        self.capture();self.s.save_intent(self.cid,0,'Human choice')
        h=self.s.save_handoff('thr_demo','blocked','Implemented',next_action='Confirm scope',blocker='Need decision',turn_at=self.now-10)
        self.capture(at=self.now+1,report='Final report')
        self.assertEqual(self.s.handoff(self.cid),h)
        self.assertEqual(self.s.intent(self.cid)['next_action'],'Human choice')
        self.assertEqual(len(self.s.attention()),1)
        a=self.s.attention()[0];self.s.acknowledge_attention([a['token']]);self.assertTrue(self.s.attention()[0]['seen'])
        self.capture('active',self.now+2,self.now+1)
        self.assertEqual(self.s.attention(),[])
        self.assertEqual(self.s.handoff(self.cid)['history'][0]['progress'],'Implemented')
    def test_stale_capture_cannot_overwrite_new_turn(self):
        self.capture('pending',self.now+2,self.now+1)
        self.capture('idle',self.now)
        self.assertEqual(self.s.handoff(self.cid)['status'],'needs_input')
    def test_failed_capture_is_actionable_without_model(self):
        self.capture('error');self.assertEqual(self.s.attention()[0]['kind'],'handoff')
        self.assertEqual(self.s._record('analysis_claim_count',0),0)
    def test_unknown_thread_and_invalid_fields_rejected(self):
        with self.assertRaises(ValueError):self.s.save_handoff('thr_unknown','paused','Done')
        self.capture()
        with self.assertRaises(ValueError):self.s.save_handoff('thr_demo','fulfilled','Done')
        with self.assertRaises(ValueError):self.s.save_handoff('thr_demo','paused','Done',turn_at=float('nan'))
    def test_context_is_bounded_and_excludes_checkpoint_history(self):
        self.capture()
        for i in range(8):self.s.save_handoff('thr_demo','paused','x'*6000,remaining='y'*3000,next_action=str(i),turn_at=self.now-10)
        packet=self.s.context_packet('thr_demo');h=packet['return_points'][0]
        self.assertNotIn('history',h);self.assertEqual(len(h['progress']),1200)
        self.assertEqual(len(self.s.handoff(self.cid)['history']),5)

    def test_portable_export_keeps_checkpoints(self):
        self.capture();export=self.s.export()
        other=Store(Path(self.tmp.name)/'other.db')
        try:other.import_snapshot(export);self.assertEqual(other.handoff(self.cid),self.s.handoff(self.cid))
        finally:other.close()

class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name)/'work.db')
        self.s.ingest('linear',[dict(external_id='TRACK-12',title='Copied request',text='I will send the report.')])
        c=self.s.claim_analysis();self.s.apply_analysis(c['id'],c['lease_token'],dict(summary='Copied',next_action='Send report',obligations=[dict(title='Send report',owner='Alex',direction='i_owe',state='open',due_at=time.time()-10,quote='I will send the report.',confidence=.99)]))
        self.oid=self.s.attention()[0]['entity_id']
    def tearDown(self):self.s.close();self.tmp.cleanup()
    def test_retirement_preserves_unique_commitments_and_evidence(self):
        original=dict(self.s.db.execute('SELECT * FROM obligations').fetchone())
        self.s.configure_tracking(['TRACK'])
        self.assertEqual(self.s.attention(),[]);page=self.s.legacy_page()
        self.assertEqual((page['total'],page['due']),(1,1))
        self.assertEqual(dict(self.s.db.execute('SELECT * FROM obligations').fetchone()),original)
        self.s.keep_legacy(self.oid,original['version']);self.assertEqual(len(self.s.attention()),1)
        self.assertEqual(self.s.legacy_page()['total'],0)
    def test_no_new_extraction_from_mirrors_and_real_team_unaffected(self):
        self.s.configure_tracking(['TRACK'])
        self.s.ingest('linear',[dict(external_id='TRACK-13',title='Mirror',text='New request'),dict(external_id='TEAM-13',title='Actual issue',text='Real work')])
        c=self.s.claim_analysis();self.assertEqual(c['title'],'Actual issue')
        self.assertEqual(self.s.work_detail(digest('linear','TRACK-13'))['work']['conversations'][0]['role'],'tracking_archive')
    def test_inflight_extraction_is_invalidated_and_policy_is_reversible(self):
        self.s.ingest('linear',[dict(external_id='TRACK-12',title='Copied',text='New promise',version='v2')]);c=self.s.claim_analysis()
        self.s.configure_tracking(['TRACK'])
        with self.assertRaises(ValueError):self.s.apply_analysis(c['id'],c['lease_token'],dict(summary='No',next_action='No',obligations=[]))
        self.s.configure_tracking([]);self.assertIsNotNone(self.s.claim_analysis());self.assertEqual(len(self.s.attention()),1)
    def test_stale_keep_and_completion_sweep_do_not_lose_work(self):
        self.s.configure_tracking(['TRACK'])
        with self.assertRaises(ValueError):self.s.keep_legacy(self.oid,999)
        self.assertEqual(self.s.queue_completion('open')['queued'],0)
        self.assertIsNone(self.s.claim_completion())
        self.assertEqual(self.s.legacy_page()['total'],1)

    def test_mirror_does_not_make_original_source_matching_ambiguous(self):
        link='https://example.slack.com/archives/C123/p1794900000000000'
        self.s.ingest('slack',[dict(external_id='C123:1794900000.000000',title='Original',url=link,text='Actual request')])
        self.s.ingest('linear',[dict(external_id='TRACK-15',title='Mirror',text=link)])
        self.s.configure_tracking(['TRACK'])
        packet=self.s.ensure_context('thr_new','Handle request',link)
        self.assertEqual(packet['work']['id'],digest('slack','C123:1794900000.000000'))
        self.assertEqual(packet['candidates'],[])

    def test_legacy_rows_cannot_starve_regular_due_reminders(self):
        original=dict(self.s.db.execute('SELECT * FROM obligations').fetchone())
        with self.s.db:
            for i in range(55):
                row={**original,'id':'legacy_'+str(i),'due_at':original['due_at']-100}
                self.s.db.execute('INSERT INTO obligations VALUES ('+','.join('?' for _ in row)+')',list(row.values()))
        self.s.configure_tracking(['TRACK']);self.s.keep_legacy(self.oid,original['version'])
        from datetime import datetime,timezone
        from unittest.mock import patch
        now=datetime(2026,11,17,15,tzinfo=timezone.utc).timestamp()
        with self.s.db:self.s.db.execute('UPDATE obligations SET due_at=? WHERE id=?',(now-1,self.oid))
        with patch('store.local_timezone',return_value=timezone.utc):claim=self.s.claim_reminder(now)
        self.assertIsNotNone(claim);self.assertEqual(self.s.reminder_payload(claim['id'])['count'],1)
