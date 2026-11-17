import tempfile,time,unittest
from pathlib import Path
from store import Store,digest

class AgentThreadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.s=Store(Path(self.temp.name)/'work.db')
        self.s.ingest('github',[dict(external_id='pr',title='Review PR',text='PR evidence',version='1')],checkpoint='cursor',scope='test')
        self.work=digest('github','pr')
    def tearDown(self):self.s.close();self.temp.cleanup()
    def register(self):return self.s.register_agent_thread(self.work,'thr_test','Review PR','codex',time.time())
    def test_link_exists_before_first_collection_and_preserves_source_checkpoint(self):
        self.assertTrue(self.register()['ok'])
        self.assertEqual(self.s.db.execute('select work_id from conversations where external_id="thr_test"').fetchone()[0],self.work)
        self.assertEqual(self.s.db.execute('select checkpoint from sources where name="github"').fetchone()[0],'"cursor"')
        self.assertEqual(self.s.db.execute('select state from sources where name="bb"').fetchone()[0],'not_connected')
        self.s.ingest('bb',[dict(external_id='thr_test',title='Review PR',text='Started review')])
        self.assertEqual(self.s.db.execute('select work_id from conversations where external_id="thr_test"').fetchone()[0],self.work)
    def test_retry_is_idempotent_and_respects_later_manual_regrouping(self):
        self.register();self.register()
        self.assertEqual(self.s.db.execute("select count(*) from changes where kind='agent_thread'").fetchone()[0],1)
        self.s.ingest('github',[dict(external_id='other',title='Other',text='Other')])
        target=digest('github','other');self.s.mutate('conversation',digest('bb','thr_test'),'link',value=target)
        self.register()
        self.assertEqual(self.s.db.execute('select work_id from conversations where external_id="thr_test"').fetchone()[0],target)
    def test_unknown_work_does_not_create_orphan(self):
        result=self.s.register_agent_thread('missing','thr_missing','Missing','codex',time.time())
        self.assertFalse(result['ok']);self.assertEqual(self.s.db.execute('select count(*) from conversations where source="bb"').fetchone()[0],0)
    def test_new_agent_reopens_work_but_reconciliation_respects_later_done(self):
        self.s.mutate('work',self.work,'done');self.register()
        self.assertEqual(self.s.db.execute('select status from work where id=?',(self.work,)).fetchone()[0],'active')
        self.s.mutate('work',self.work,'done');self.register()
        self.assertEqual(self.s.db.execute('select status from work where id=?',(self.work,)).fetchone()[0],'done')
