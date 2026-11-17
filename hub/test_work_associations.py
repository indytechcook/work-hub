import tempfile,time,unittest
from pathlib import Path
from store import Store,digest

class WorkAssociationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.s=Store(Path(self.temp.name)/'work.db')
        self.s.ingest('bb',[dict(external_id='thr_one',title='One',text='I will send it. Use polling.')])
        c=self.s.claim_analysis()
        self.s.apply_analysis(c['id'],c['lease_token'],dict(summary='One summary',next_action='Send it',decisions=[dict(text='Polling',quote='Use polling.')],obligations=[dict(title='Send it',owner='Alex',direction='i_owe',due_at=time.time()-60,state='open',quote='I will send it.',confidence=.99,explicit_resolution=False)]))
        self.s.ingest('github',[dict(external_id='pr',title='Two',text='Other context')])
        self.one=digest('bb','thr_one');self.two=digest('github','pr')
    def tearDown(self):self.s.close();self.temp.cleanup()
    def add(self):return self.s.associate(self.one,self.two,'add')
    def test_add_projects_one_conversation_decision_and_obligation_into_both_work_items(self):
        self.add();self.add()
        snapshot=self.s.snapshot();works={w['id']:w for w in snapshot['work']}
        for wid in [self.one,self.two]:
            self.assertEqual(sum(c['id']==self.one for c in works[wid]['conversations']),1)
            self.assertEqual(works[wid]['decisions'][0]['text'],'Polling')
        self.assertEqual(len(snapshot['obligations']),1)
        o=snapshot['obligations'][0];self.assertEqual(set(o['work_ids']),{self.one,self.two})
        self.assertEqual(len(self.s.thread_context('thr_one')['associated_work']),2)
        self.assertEqual(self.s.thread_context('thr_one',self.two)['work']['id'],self.two)
        self.s.mutate('obligation',o['id'],'fulfilled',version=o['version'])
        self.assertEqual(self.s.thread_context('thr_one',self.one)['obligations'],[])
        self.assertEqual(self.s.thread_context('thr_one',self.two)['obligations'],[])
    def test_removing_primary_promotes_other_without_losing_evidence(self):
        self.add();self.s.associate(self.one,self.one,'remove')
        c=self.s.thread_context('thr_one',self.one)
        self.assertEqual(c['work']['id'],self.two)
        self.assertEqual(c['obligations'][0]['evidence_quote'],'I will send it.')
        self.assertEqual(c['obligations'][0]['work_id'],self.two)
        self.assertEqual(c['obligations'][0]['work_ids'],[self.two])
        with self.assertRaises(ValueError):self.s.associate(self.one,self.two,'remove')
        self.assertEqual(self.s._work_ids(self.one),[self.two])
    def test_shared_links_survive_reingestion_and_agent_metadata_reconciliation(self):
        self.s.register_agent_thread(self.one,'thr_agent','Agent','codex',time.time())
        cid=digest('bb','thr_agent');self.s.associate(cid,self.two,'add')
        self.s.ingest('bb',[dict(external_id='thr_agent',parent_id='thr_one',title='Agent',text='Working')])
        self.s.register_agent_thread(self.one,'thr_agent','Agent','codex',time.time())
        self.assertEqual(set(self.s._work_ids(cid)),{self.one,self.two})
        self.s.associate(cid,self.one,'remove')
        self.s.register_agent_thread(self.one,'thr_agent','Agent','codex',time.time())
        self.assertEqual(self.s._work_ids(cid),[self.two])
    def test_remove_extra_preserves_primary_and_unknown_target_is_atomic(self):
        self.add();self.s.associate(self.one,self.two,'remove');self.s.associate(self.one,self.two,'remove')
        self.assertEqual(self.s._work_ids(self.one),[self.one])
        with self.assertRaises(ValueError):self.s.associate(self.one,'unknown','add')
        with self.assertRaises(ValueError):self.s.associate('unknown',self.two,'add')
        self.assertEqual(self.s._work_ids(self.one),[self.one])
    def test_scope_cannot_select_unrelated_work_and_options_cover_non_dashboard_work(self):
        self.assertNotIn(self.two,[w['id'] for w in self.s.snapshot()['work']])
        options=self.s.work_options(self.one,'Two')
        self.assertEqual([w['id'] for w in options['options']],[self.two])
        self.assertEqual(self.s.thread_context('thr_one',self.two)['work']['id'],self.one)
        self.assertEqual(self.s.work_options(self.one,'%')['options'],[])
    def test_round_trip_v3_and_legacy_v1_import(self):
        self.add();payload=self.s.export();self.assertEqual(payload['schema_version'],3)
        other=Store(Path(self.temp.name)/'roundtrip.db')
        try:
            other.import_snapshot(payload)
            self.assertEqual(set(other._work_ids(self.one)),{self.one,self.two})
            self.assertEqual(len(other.thread_context('thr_one',self.two)['obligations']),1)
        finally:other.close()
        payload['schema_version']=1;del payload['tables']['conversation_work_links']
        legacy=Store(Path(self.temp.name)/'legacy.db')
        try:
            legacy.import_snapshot(payload)
            self.assertEqual(legacy._work_ids(self.one),[self.one])
        finally:legacy.close()
    def test_reopen_keeps_old_single_associations_and_shared_links(self):
        self.add();path=self.s.path;self.s.close();self.s=Store(path)
        self.assertEqual(set(self.s._work_ids(self.one)),{self.one,self.two})
        self.assertEqual(self.s._work_ids(self.two),[self.two])
