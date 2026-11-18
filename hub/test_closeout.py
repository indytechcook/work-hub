import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from closeout import cycle, nightly_due, reconcile, related_candidates, run
from store import Store, digest
from test_support import isolate_storage


class CloseOutTests(unittest.TestCase):
    def setUp(self):
        isolate_storage(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name)/'test.db')
        self.addCleanup(self.store.close)
        self.pr = 'https://github.com/example/fixture/pull/1'
        self.store.ingest('github',[dict(external_id='p1',title='PR',url=self.pr,text='Review')])
        self.store.ingest('slack',[dict(external_id='s1',title='Thread',text='See '+self.pr)])
        self.cid = digest('github','p1')
        self.wid = digest('slack','s1')
        self.sources = ['github','slack']

    def secondary(self):
        with self.store.db:
            self.store.db.execute('INSERT INTO conversation_work_links VALUES (?,?)',(self.cid,self.wid))

    def test_repair_then_fold_once(self):
        with self.store.db:
            self.store.db.execute('DELETE FROM source_references WHERE conversation_id=?',(self.wid,))
        self.assertEqual(reconcile(self.store,self.sources,False), (1,0,0,0))
        self.assertEqual(len(related_candidates(self.store,self.sources)),1)
        self.secondary()
        self.assertEqual(reconcile(self.store,self.sources,False), (0,1,1,0))
        self.assertEqual(self.store._work_ids(self.cid),[self.wid])
        self.assertEqual(reconcile(self.store,self.sources,False), (0,0,0,0))

    def test_manual_association_and_planned_work_survive(self):
        self.store.associate(self.cid,self.wid,'add')
        self.assertEqual(reconcile(self.store,self.sources,False)[1],0)
        with self.store.db:
            self.store.db.execute('DELETE FROM manual_links')
            self.store.db.execute("DELETE FROM changes WHERE kind='association'")
        self.store.save_day_plan('2026-01-10',0,[self.cid])
        self.assertEqual(reconcile(self.store,self.sources,False)[1],0)

    def test_source_failure_still_saves_local_review(self):
        with patch('closeout.selected_sources',return_value=['slack']),patch('closeout.source_check',side_effect=RuntimeError):
            report = run(self.store)
        self.assertEqual(report['coverage'][0]['state'],'failed')
        self.assertEqual(report,self.store._record('closeout:last',None))
        self.assertFalse(nightly_due(self.store))
        self.assertEqual(cycle(datetime(2026,3,9,7)),'2026-03-08')
        self.assertEqual(cycle(datetime(2026,3,9,22)),'2026-03-09')
