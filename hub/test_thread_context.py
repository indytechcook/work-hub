import tempfile,time,unittest
from pathlib import Path
from store import Store,digest

class ThreadContextTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.s=Store(Path(self.temp.name)/'work.db')
        self.s.ingest('bb',[dict(external_id='thr_main',title='Main',text='I will send the draft. Use local polling.')])
        c=self.s.claim_analysis()
        self.s.apply_analysis(c['id'],c['lease_token'],dict(summary='Draft',next_action='Send draft',decisions=[dict(text='Local polling',quote='Use local polling.')],obligations=[dict(title='Send draft',owner='Alex',direction='i_owe',due_at=None,state='open',quote='I will send the draft.',confidence=.99,explicit_resolution=False)]))
        self.s.ingest('bb',[dict(external_id='thr_child',parent_id='thr_main',title='Side chat',text='Working on draft')])
        self.s.ingest('github',[dict(external_id='pr',title='Related PR',url='https://github.com/example/app/pull/1',text='Change')])
        self.s.mutate('conversation',digest('github','pr'),'link',value=digest('bb','thr_main'))
        self.s.ingest('bb',[dict(external_id='thr_other',title='Unrelated',text='Other work')])
    def tearDown(self):self.s.close();self.temp.cleanup()
    def test_child_gets_shared_sources_decisions_and_commitments_without_unrelated_work(self):
        context=self.s.thread_context('thr_child')
        self.assertEqual(context['work']['id'],digest('bb','thr_main'))
        self.assertEqual({c['external_id'] for c in context['work']['conversations']},{'thr_main','thr_child','pr'})
        self.assertEqual(context['work']['decisions'][0]['text'],'Local polling')
        self.assertEqual(context['obligations'][0]['evidence_quote'],'I will send the draft.')
        o=context['obligations'][0];self.s.mutate('obligation',o['id'],'fulfilled',version=o['version'])
        self.assertEqual(self.s.thread_context('thr_child')['obligations'],[])
    def test_direct_lookup_survives_dashboard_cap_and_respects_manual_relink(self):
        self.s.ingest('bb',[dict(external_id=f'thr_noise_{i}',title='Noise',text='',occurred_at=time.time()+i+1) for i in range(205)])
        self.assertNotIn(digest('bb','thr_main'),[w['id'] for w in self.s.snapshot()['work']])
        self.assertEqual(self.s.thread_context('thr_child')['work']['title'],'Main')
        self.s.mutate('conversation',digest('bb','thr_child'),'link',value=digest('bb','thr_other'))
        self.assertEqual(self.s.thread_context('thr_child')['work']['title'],'Unrelated')
        self.assertEqual(self.s.thread_context('thr_child')['obligations'],[])
    def test_unknown_thread_is_empty_and_bad_id_is_rejected(self):
        self.assertIsNone(self.s.thread_context('thr_missing')['work'])
        self.assertEqual(self.s.thread_context('thr_missing')['obligations'],[])
        with self.assertRaises(ValueError):self.s.thread_context('not-a-thread')
