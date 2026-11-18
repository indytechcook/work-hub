import tempfile,time,unittest
from pathlib import Path
from store import Store,digest

class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.s=Store(Path(self.temp.name)/'work.db')
        self.body='I will send the checklist.'
        self.s.ingest('bb',[dict(external_id='thr_release',title='Release',text=self.body,version='v1')])
        self.wid=digest('bb','thr_release');self.analyze()
    def tearDown(self):self.s.close();self.temp.cleanup()
    def analyze(self,due=None):
        c=self.s.claim_analysis()
        self.s.apply_analysis(c['id'],c['lease_token'],dict(summary='Captured progress',next_action='Model suggestion',obligations=[dict(title='Send checklist',owner='Alex',direction='i_owe',due_at=due,state='open',quote=self.body,confidence=.99)]))
    def test_standalone_work_is_findable_without_a_source(self):
        saved = self.s.create_work('Write setup guide', 'Document the setup', 'fixture-request')
        wid = saved['id']
        self.assertTrue(saved['created'])
        detail = self.s.work_detail(wid)['work']
        self.assertEqual(detail['conversations'], [])
        self.assertEqual(detail['intent']['purpose'], 'Document the setup')
        self.assertEqual(self.s.work_index('Write setup guide')['items'][0]['id'], wid)
        self.assertIn(wid, [w['id'] for w in self.s.snapshot()['work']])
        self.assertIn(wid, [w['id'] for w in self.s.work_options(self.wid)['options']])
        self.s.associate(self.wid, wid, 'add')
        self.s.associate(self.wid, wid, 'remove')
        self.s.rename_work(wid, detail['title_version'], 'New guide name')
        self.assertEqual(self.s.create_work('Write setup guide', 'Document the setup', 'fixture-request'),
                         dict(id=wid, created=False))
        with self.assertRaises(ValueError):
            self.s.create_work('Different work', request_id='fixture-request')
        restored = Store(Path(self.temp.name)/'restored.db')
        self.addCleanup(restored.close)
        restored.import_snapshot(self.s.export())
        self.assertEqual(restored.work_index('New guide name')['items'][0]['id'], wid)

    def test_standalone_creation_retries_without_request_id(self):
        first = self.s.create_work('Draft checklist')
        self.assertEqual(self.s.create_work(' Draft checklist '), dict(id=first['id'], created=False))
        for title, purpose in [('', ''), ('Invalid\ntitle', ''), ('Valid', 'a'*3001)]:
            with self.assertRaises(ValueError):
                self.s.create_work(title, purpose)

    def test_chosen_intent_survives_later_analysis_and_reopen(self):
        self.s.save_intent(self.wid,0,'Check the boundary',purpose='Safe release',project='Atlas',thread_id='thr_release')
        self.s.ingest('bb',[dict(external_id='thr_release',title='Release',text=self.body,version='v2')]);self.analyze()
        w=self.s.work_detail(self.wid)['work']
        self.assertEqual(w['intent']['next_action'],'Check the boundary');self.assertEqual(w['next_action'],'Model suggestion')
        self.assertEqual(w['conversations'][0]['checkpoint']['event_id'],digest(self.wid,'v2'))
        self.assertEqual(w['conversations'][0]['analysis_state'],'done')
        self.s.close();self.s=Store(Path(self.temp.name)/'work.db')
        self.assertEqual(self.s.intent(self.wid)['project'],'Atlas')
        with self.assertRaises(ValueError):self.s.save_intent(self.wid,0,'Stale overwrite')
        with self.assertRaises(ValueError):self.s.save_intent(self.wid,1,'x',thread_id='thr_unrelated')
        self.assertEqual(self.s.intent(self.wid)['version'],1)
    def test_focus_and_unfocus_do_not_reopen_done_work_or_resolve_obligation(self):
        self.s.mutate('work',self.wid,'done');self.s.mutate('work',self.wid,'focus');self.s.mutate('work',self.wid,'unfocus')
        self.assertEqual(self.s.work_detail(self.wid)['work']['status'],'done')
        self.assertEqual(self.s.work_detail(self.wid)['obligations'][0]['state'],'open')
    def test_multiple_focus_items_survive_restart_and_independent_removal(self):
        self.s.ingest('bb',[dict(external_id='thr_second',title='Second',text='Context')])
        second=digest('bb','thr_second')
        self.s.mutate('work',self.wid,'waiting')
        self.s.mutate('work',self.wid,'focus')
        self.s.mutate('work',second,'focus')
        self.s.close();self.s=Store(Path(self.temp.name)/'work.db')
        self.assertEqual({w['id'] for w in self.s.snapshot()['work'] if w['focus']},{self.wid,second})
        self.assertEqual(self.s.work_detail(self.wid)['work']['status'],'waiting')
        self.s.mutate('work',second,'unfocus')
        self.assertEqual({w['id'] for w in self.s.snapshot()['work'] if w['focus']},{self.wid})
        self.assertEqual(self.s.work_detail(self.wid)['obligations'][0]['state'],'open')

    def test_seen_is_separate_from_resolution_and_unchanged_reanalysis(self):
        a=self.s.attention()[0];self.s.acknowledge_attention([a['token']])
        self.assertTrue(self.s.attention()[0]['seen'])
        self.assertEqual(self.s.snapshot()['obligations'][0]['state'],'open')
        self.s.ingest('bb',[dict(external_id='thr_release',title='Release',text=self.body,version='v2')]);self.analyze()
        self.assertEqual(self.s.attention()[0]['token'],a['token']);self.assertTrue(self.s.attention()[0]['seen'])
        o=self.s.snapshot()['obligations'][0];self.s.mutate('obligation',o['id'],'fulfilled',version=o['version'],value='Delivered in the source thread')
        self.assertEqual(self.s.attention(),[])
        self.assertEqual(self.s.work_detail(self.wid)['obligations'][0]['resolution'],'Delivered in the source thread')
    def test_meaningful_date_change_realerts_and_stale_ack_does_not_hide_it(self):
        old=self.s.attention()[0]
        self.s.ingest('bb',[dict(external_id='thr_release',title='Release',text=self.body,version='v2')]);self.analyze(time.time()-30)
        new=self.s.attention()[0];self.assertNotEqual(old['token'],new['token'])
        self.s.acknowledge_attention([old['token']]);self.assertFalse(self.s.attention()[0]['seen'])
    def test_one_shared_commitment_has_one_attention_identity(self):
        self.s.ingest('bb',[dict(external_id='thr_pilot',title='Pilot',text='Other')]);other=digest('bb','thr_pilot')
        self.s.associate(self.wid,other,'add');a=self.s.attention()
        self.assertEqual(len(a),1);self.assertEqual(set(a[0]['work_ids']),{self.wid,other})
        self.s.acknowledge_attention([a[0]['token']]);self.assertTrue(self.s.attention()[0]['seen'])
    def test_day_plan_is_versioned_portable_and_has_no_focus_or_deadline_side_effect(self):
        date=self.s.day_plan()['date'];self.s.save_day_plan(date,0,[self.wid],'Start with the test')
        with self.assertRaises(ValueError):self.s.save_day_plan(date,0,[])
        with self.assertRaises(ValueError):self.s.save_day_plan(date,1,['missing'])
        self.assertEqual(self.s.day_plan()['version'],1)
        self.assertEqual(self.s.work_detail(self.wid)['work']['focus'],0)
        other=Store(Path(self.temp.name)/'import.db')
        try:
            other.import_snapshot(self.s.export());self.assertEqual(other.day_plan(),self.s.day_plan())
        finally:other.close()
    def test_work_search_and_detail_find_outside_old_snapshot_limit(self):
        self.s.ingest('bb',[dict(external_id=f'thr_noise_{i}',title=f'Noise {i}',text='') for i in range(210)])
        self.assertEqual(self.s.work_index('Release')['items'][0]['id'],self.wid)
        self.assertEqual(self.s.work_index()['total'],211)
        self.assertEqual(len(self.s.work_index(offset=200)['items']),11)
        self.assertEqual(self.s.work_detail(self.wid)['obligations'][0]['title'],'Send checklist')
    def test_unanalyzed_raw_source_is_not_falsely_important(self):
        self.s.ingest('betterstack',[dict(external_id='error',title='Signal',text='Unverified impact')])
        self.assertEqual(len(self.s.attention()),1)
        w=self.s.work_detail(digest('betterstack','error'))['work']
        self.assertIsNone(w['conversations'][0]['checkpoint'])
        self.assertEqual(w['conversations'][0]['analysis_state'],'pending')
    def test_previous_return_note_survives_a_new_day(self):
        self.s.save_day_plan('2020-01-02',0,[self.wid],'Start at the boundary test')
        self.assertEqual(self.s.previous_day_plan()['note'],'Start at the boundary test')
        self.assertEqual(self.s.day_plan()['note'],'')
    def test_reviewing_a_capped_change_page_preserves_hidden_and_later_changes(self):
        for n in range(120):self.s.change('fixture',str(n),'Routine update')
        self.s.db.commit()
        shown=self.s.snapshot()['changes'];self.assertEqual(len(shown),100)
        self.s.change('fixture','new','Arrived while reviewing');self.s.db.commit()
        self.s.acknowledge_changes([c['seq'] for c in shown])
        remaining=self.s.snapshot()['changes']
        self.assertTrue(any(c['entity_id']=='new' for c in remaining))
        self.assertTrue(any(c['seq']<min(x['seq'] for x in shown) for c in remaining))
        self.assertFalse({c['seq'] for c in remaining}&{c['seq'] for c in shown})
        self.assertEqual(len(self.s.snapshot()['obligations']),1)
