"""Rename regression checks against temporary, synthetic ledgers only."""
import tempfile
import unittest
from pathlib import Path
from store import Store, digest
from test_source_preview import MESSAGE
from test_support import isolate_storage


class WorkNameTests(unittest.TestCase):
    def setUp(self):
        isolate_storage(self)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'work.db'
        self.s=Store(self.path);self.addCleanup(lambda:self.s.close())
        self.s.ingest('bb',[dict(external_id='thr_example',title='Source conversation',text='I will send the checklist.',version='v1')])
        self.wid=digest('bb','thr_example')

    def test_only_work_name_and_revision_change(self):
        claim=self.s.claim_analysis()
        self.s.apply_analysis(claim['id'],claim['lease_token'],dict(summary='Progress',next_action='Review',decisions=[dict(text='Use a checklist',quote='checklist')],obligations=[dict(title='Send checklist',owner='Sam',direction='i_owe',state='open',quote='I will send the checklist.',confidence=.99)]))
        self.s.mutate('work',self.wid,'focus');self.s.mutate('work',self.wid,'waiting')
        self.s.save_intent(self.wid,0,'Chosen next step')
        self.s.save_day_plan(self.s.day_plan()['date'],0,[self.wid])
        before=self.s.export()['tables']
        self.assertEqual(self.s.rename_work(self.wid,0,'  Launch readiness  '),dict(title='Launch readiness',title_version=1))
        after=self.s.export()['tables']
        for table in before:
            if table not in ('work','meta','changes'):self.assertEqual(before[table],after[table],table)
        expected={**before['work'][0],'title':'Launch readiness','updated_at':after['work'][0]['updated_at']}
        self.assertEqual(after['work'][0],expected)
        self.assertEqual(self.s.day_plan()['work_ids'],[self.wid])
        self.assertEqual(self.s.intent(self.wid)['next_action'],'Chosen next step')
        self.assertEqual(self.s.context_packet('thr_example')['work']['title'],'Launch readiness')
        self.assertEqual(self.s.work_index('Launch readiness')['items'][0]['id'],self.wid)
        self.assertEqual(self.s.snapshot()['work'][0]['title'],'Launch readiness')

    def test_collection_analysis_restart_and_export_keep_name(self):
        # A lease acquired before rename may finish afterwards.
        claim=self.s.claim_analysis();self.s.rename_work(self.wid,0,'My work')
        self.s.apply_analysis(claim['id'],claim['lease_token'],dict(summary='Progress',next_action='Check again',obligations=[]))
        self.s.ingest('bb',[dict(external_id='thr_example',title='Updated source title',text='New progress',version='v2')])
        claim=self.s.claim_analysis()
        self.s.apply_analysis(claim['id'],claim['lease_token'],dict(summary='Latest',next_action='Continue',obligations=[]))
        self.s.close();self.s=Store(self.path)
        work=self.s.work_detail(self.wid)['work']
        self.assertEqual((work['title'],work['title_version']),('My work',1))
        self.assertEqual(work['conversations'][0]['title'],'Updated source title')
        restored=Store(Path(self.tmp.name)/'restored.db');self.addCleanup(restored.close)
        restored.import_snapshot(self.s.export())
        self.assertEqual(restored.work_detail(self.wid)['work']['title'],'My work')
        with self.assertRaisesRegex(ValueError,'changed in another view'):restored.rename_work(self.wid,0,'stale')

    def test_concurrent_views_and_aba_cannot_overwrite(self):
        other=Store(self.path);self.addCleanup(other.close)
        version=other.work_detail(self.wid)['work']['title_version']
        self.s.rename_work(self.wid,version,'Second')
        self.s.rename_work(self.wid,version+1,'Source conversation')
        before=self.s.export()['tables']
        with self.assertRaisesRegex(ValueError,'changed in another view'):other.rename_work(self.wid,version,'Stale edit')
        self.assertEqual(self.s.export()['tables'],before)
        other.rename_work(self.wid,2,'Reviewed new name')
        self.assertEqual(self.s.work_detail(self.wid)['work']['title'],'Reviewed new name')

    def test_invalid_names_and_versions_are_atomic(self):
        before=self.s.export()['tables']
        for title in ('','  ','x'*201,'line\nbreak','tab\tname','null\0name','line\u2028break',None,42):
            with self.subTest(title=title),self.assertRaises(ValueError):self.s.rename_work(self.wid,0,title)
        for version in (None,True,-1,0.0,'0'):
            with self.subTest(version=version),self.assertRaises(ValueError):self.s.rename_work(self.wid,version,'Valid')
        with self.assertRaisesRegex(ValueError,'Unknown work'):self.s.rename_work('missing',0,'Valid')
        self.assertEqual(self.s.export()['tables'],before)
        self.s.rename_work(self.wid,0,'🚀'*200)
        self.assertEqual(self.s.work_detail(self.wid)['work']['title'],'🚀'*200)

    def test_detail_never_pairs_an_old_row_title_with_a_new_version(self):
        row=self.s.db.execute('SELECT * FROM work WHERE id=?',(self.wid,)).fetchone()
        self.s.rename_work(self.wid,0,'Concurrent name')
        detail=self.s._work_detail(row)
        self.assertEqual((detail['title'],detail['title_version']),('Concurrent name',1))

    def test_explicit_slack_names_win_everywhere_and_keep_previews(self):
        self.s.ingest('slack',[dict(external_id='source',title='Slack · DM',text=MESSAGE)])
        wid=digest('slack','source')
        for version,title in enumerate(('Slack · My launch plan','Slack conversation')):
            self.s.rename_work(wid,version,title)
            self.assertEqual(self.s.work_options(self.wid)['options'][0]['title'],title)
            self.s.associate(self.wid,wid,'add')
            self.assertEqual(next(w for w in self.s.work_options(self.wid)['linked'] if w['id']==wid)['title'],title)
            context=self.s.thread_context('thr_example',wid)
            self.assertEqual(context['work']['title'],title)
            self.assertEqual(next(w for w in context['associated_work'] if w['id']==wid)['title'],title)
            self.assertEqual(next(w for w in self.s.work_index()['items'] if w['id']==wid)['title'],title)
            source=next(c for c in context['work']['conversations'] if c['source']=='slack')
            self.assertIn('Sam Example',source['title']);self.assertIn('Please review',source['preview']['text'])
            self.s.associate(self.wid,wid,'remove')
        self.s.ingest('slack',[dict(external_id='source',title='Slack · Refreshed source',text=MESSAGE+' updated')],targeted=True)
        self.assertEqual(self.s.work_detail(wid)['work']['title'],'Slack conversation')

    def test_late_parent_does_not_hide_named_work(self):
        self.s.ingest('bb',[dict(external_id='thr_child',parent_id='thr_parent',title='Child',text='Progress')])
        child=digest('bb','thr_child');self.s.rename_work(child,0,'Chosen work')
        self.s.ingest('bb',[dict(external_id='thr_parent',title='Parent',text='Context')])
        self.assertEqual(self.s.thread_context('thr_child')['work']['id'],child)
        self.assertEqual(self.s.thread_context('thr_child')['work']['title'],'Chosen work')

    def test_context_refresh_keeps_named_source_work_visible(self):
        url='https://example.slack.com/archives/C123/p1794900000000000'
        self.s.ensure_context('thr_new','New work',url)
        claim=self.s.claim_context('thr_new')
        self.s.ingest('slack',[dict(external_id='source',title='Slack · DM',url=url,text=MESSAGE)])
        wid=digest('slack','source');self.s.rename_work(wid,0,'Slack · Important request')
        self.s.finish_context(claim,wid)
        self.assertEqual(self.s.db.execute('SELECT work_id FROM conversations WHERE id=?',(wid,)).fetchone()[0],wid)
        self.assertIn(wid,[w['id'] for w in self.s.work_index()['items']])
        self.assertIn(wid,[c['id'] for c in self.s.thread_context('thr_new')['work']['conversations']])
