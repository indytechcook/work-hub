import tempfile,unittest
from pathlib import Path
from store import Store,digest
from labels import slack_label
from source_preview import slack_preview
from test_source_preview import MESSAGE

class SlackLabelTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Store(Path(self.tmp.name)/'work.db')
        self.title='Slack · DM (ID: DEXAMPLE)';self.external='DEXAMPLE:1794930000.123456'
        self.s.ingest('slack',[dict(external_id=self.external,title=self.title,text=MESSAGE,url='https://example.slack.com/archives/DEXAMPLE/p1794930000123456')])
        self.wid=digest('slack',self.external)
        self.s.ingest('bb',[dict(external_id='thr_related',title='Review release',text='Related work')]);self.related=digest('bb','thr_related')
        self.expected=slack_label(self.title,slack_preview(MESSAGE),self.external)
    def tearDown(self):self.s.close();self.tmp.cleanup()
    def test_label_identifies_author_time_and_message_without_ids_or_envelope(self):
        self.assertIn('Sam Example',self.expected);self.assertIn('Nov 17, 2026',self.expected)
        self.assertIn('Please review PR #12 with @Alex & confirm.',self.expected)
        self.assertNotIn('DEXAMPLE',self.expected);self.assertNotIn('Message TS:',self.expected)
    def test_labels_are_consistent_across_associations_lists_details_and_context(self):
        options=self.s.work_options(self.related)['options'];self.assertEqual(options[0]['title'],self.expected)
        self.s.associate(self.related,self.wid,'add')
        self.assertEqual(next(x for x in self.s.work_options(self.related)['linked'] if x['id']==self.wid)['title'],self.expected)
        self.assertEqual(next(x for x in self.s.work_index()['items'] if x['id']==self.wid)['title'],self.expected)
        detail=self.s.work_detail(self.wid)['work'];self.assertEqual(detail['title'],self.expected)
        self.assertEqual(next(c for c in detail['conversations'] if c['source']=='slack')['title'],self.expected)
        context=self.s.thread_context('thr_related',self.wid)
        self.assertEqual(context['work']['title'],self.expected)
        self.assertEqual(next(x for x in context['associated_work'] if x['id']==self.wid)['title'],self.expected)
        self.assertEqual(next(c for c in self.s.snapshot()['changes'] if c['entity_id']==self.wid)['detail'],self.expected)
        # Presentation is not a migration of stored work or source titles.
        self.assertEqual(self.s.db.execute('SELECT title FROM work WHERE id=?',(self.wid,)).fetchone()[0],self.title)
        self.assertEqual(self.s.db.execute('SELECT title FROM conversations WHERE id=?',(self.wid,)).fetchone()[0],self.title)
    def test_search_matches_author_and_text_before_pagination(self):
        for q in ('sam example','Please review','@Alex'):
            self.assertEqual([w['id'] for w in self.s.work_options(self.related,q)['options']],[self.wid])
            self.assertEqual([w['id'] for w in self.s.work_index(q)['items']],[self.wid])
        self.assertEqual(self.s.work_options(self.related,'%')['options'],[])
    def test_other_sources_and_custom_work_names_stay_intact(self):
        self.assertEqual(self.s.work_detail(self.related)['work']['title'],'Review release')
        with self.s.db:self.s.db.execute('UPDATE work SET title=? WHERE id=?',('Release planning',self.wid))
        self.assertEqual(self.s.work_detail(self.wid)['work']['title'],'Release planning')
        self.assertEqual(self.s.work_detail(self.wid)['work']['conversations'][0]['title'],self.expected)
    def test_missing_preview_uses_thread_time_without_inventing_person(self):
        label=slack_label(self.title,None,self.external)
        self.assertIn('Nov 17, 2026',label);self.assertIn('Message preview unavailable',label)
        self.assertNotIn('Sam',label)
    def test_same_channel_different_threads_are_distinguishable(self):
        other=MESSAGE.replace('Please review','Please ship')
        self.s.ingest('slack',[dict(external_id='DEXAMPLE:1794930060.123456',title=self.title,text=other)])
        labels=[x['title'] for x in self.s.work_options(self.related)['options']]
        self.assertEqual(len(set(labels)),2)
    def test_long_excerpt_is_bounded_and_never_uses_replies(self):
        p=slack_preview(MESSAGE.replace('Please review','x'*400+' Please review'))
        label=slack_label(self.title,p,self.external)
        self.assertLess(len(label),240);self.assertNotIn('Private later reply',label)
