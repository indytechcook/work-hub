import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from test_support import isolate_storage
from store import Store,digest
from references import reference,references
from completion import once
from source_reads import add_source_link, hydrate_context, refresh_reference

SLACK='https://example.slack.com/archives/C123/p1794900000000000'
PR='https://github.com/example/project/pull/12'

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        isolate_storage(self)
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.s=Store(Path(folder.name)/'work.db');self.addCleanup(self.s.close)
    def source(self,source='slack',url=SLACK,body='Please review '+PR,stamp=None):
        external='source' if source=='slack' else 'pr'
        self.s.ingest(source,[{'external_id':external,'title':'Request','url':url,'text':body,'occurred_at':stamp or time.time()-10}])
        return digest(source,external)
    def commitment(self,cid=None):
        cid=cid or self.source();row=self.s.db.execute('SELECT * FROM conversations WHERE id=?',(cid,)).fetchone()
        with self.s.db:self.s.db.execute("INSERT INTO obligations(id,work_id,conversation_id,evidence_event,evidence_quote,title,owner,direction,state,updated_at) VALUES (?,?,?,?,?,'Review PR','Sam','i_owe','open',?)",('o',row['work_id'],cid,row['latest_event'],'Please review '+PR,time.time()))
        return cid
    def result(self,eid,quote='Review submitted',verdict='confirmed_complete'):
        return {'verdict':verdict,'reason':'Review delivered; merge remains separate','confidence':0.99,'citations':[{'event_id':eid,'quote':quote}]},[]
    def review(self,result=None):
        self.s.queue_completion('selected',['o'])
        with patch('completion.refresh_reference'),patch('completion.identity',return_value={'name':'Sam','email':'sam@example.com','timezone':'UTC'}),patch('completion.run',return_value=result) as model:
            response=once(self.s)
        return response,model
    def test_add_links_to_selected_work_without_a_bb_thread(self):
        work=self.source('github',PR,body='Destination')
        first=self.source(body='First message')
        second_url='https://example.slack.com/archives/C999/p1794900000000000'
        related='https://github.com/example/project/pull/14'
        def read(ref,project_id):
            return dict(external_id='other',title='Other',text=related if ref['source']=='slack' else 'PR',url=ref['url'])
        with patch('source_reads.selected_sources',return_value=['slack','github']),patch('source_reads.slack_settings',return_value={'workspace_url':'https://example.slack.com'}),patch('source_reads.read_reference',side_effect=read):
            self.assertEqual(add_source_link(self.s,work,SLACK)['state'],'added')
            self.assertEqual(add_source_link(self.s,work,second_url)['state'],'added')
            self.assertEqual(add_source_link(self.s,work,SLACK)['state'],'already_linked')
            hydrate_context(self.s,limit=3)
            self.assertCountEqual(self.s._work_ids(first),[first,work])
            urls=[c['url'] for c in self.s.work_detail(work)['work']['conversations']]
            self.assertCountEqual(urls,[PR,SLACK,second_url,related])
            with self.assertRaisesRegex(ValueError,'workspace'):add_source_link(self.s,work,SLACK.replace('example.slack','another.slack'))
            with self.assertRaisesRegex(ValueError,'Use a Slack'):add_source_link(self.s,work,'https://unsupported.example/')
        with patch('source_reads.selected_sources',return_value=[]):
            with self.assertRaisesRegex(ValueError,'not enabled'):add_source_link(self.s,work,SLACK)

    def test_exact_source_match_connects_new_thread_without_duplicate_work(self):
        cid=self.source();n=self.s.db.execute('SELECT count(*) FROM work').fetchone()[0]
        p=self.s.ensure_context('thr_new','Start work','Please do this '+SLACK)
        self.assertEqual(p['work']['id'],cid)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM work').fetchone()[0],n)
        self.s.ensure_context('thr_new','Start work','Please do this '+SLACK)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM context_requests').fetchone()[0],1)
    def test_no_match_creates_work_before_source_arrives(self):
        p=self.s.ensure_context('thr_new','New request',SLACK)
        self.assertEqual(p['work']['id'],digest('bb','thr_new'))
        claim=self.s.claim_context('thr_new');cid=self.source()
        self.s.finish_context(claim,cid)
        context=self.s.thread_context('thr_new')
        self.assertEqual({c['source'] for c in context['work']['conversations']},{'slack','bb'})
        self.assertEqual(self.s.db.execute('SELECT work_id FROM conversations WHERE id=?',(cid,)).fetchone()[0],p['work']['id'])
    def test_slack_permalink_attaches_direct_sources_once_in_either_arrival_order(self):
        issue='https://linear.app/example/issue/ENG-12'
        other='https://github.com/example/project/pull/99'
        messages=f'Parent <{PR}|{other}>.\n--- Reply 1 ---\n<{issue}|ticket> and {PR}'
        envelope=json.dumps({'messages':messages,'pagination_info':other}).replace('/',r'\/')
        thread='thr_request'
        work=self.s.ensure_context(thread,'Request',SLACK)['work']['id']
        slack_id='C123:1794900000.000000'
        def read(ref,project):
            if ref['source']=='slack':
                return dict(external_id=slack_id,title='Thread',text=envelope,url=SLACK)
            return dict(external_id=ref['id'] if ref['source']=='linear' else 'pr',
                        title='Evidence',text=other,url=ref['url'])
        with patch('source_reads.selected_sources',return_value=['github','slack','linear']),patch('source_reads.read_reference',side_effect=read) as reader:
            hydrate_context(self.s,thread,1)
            self.source('github',PR,'From scheduled collection')
            hydrate_context(self.s,thread,3)
            self.s.ingest('linear',[dict(external_id='ENG-12',title='Updated issue',text='Collected later',url=issue+'/title')])
            self.s.ensure_context(thread,'Request',SLACK)
            hydrate_context(self.s,thread,3)
            self.assertEqual(reader.call_count,2)
        sources=self.s.work_detail(work)['work']['conversations']
        self.assertCountEqual([c['source'] for c in sources],['bb','slack','github','linear'])
        self.assertEqual({r['url'] for r in self.s.context_packet(thread)['requests']},{SLACK,PR,issue})
        self.assertEqual({r[0] for r in self.s.db.execute('SELECT DISTINCT work_id FROM work_memberships')},{work})
        self.assertEqual(self.s.ensure_context('thr_repeat','Request',SLACK)['work']['id'],work)
        self.assertEqual(self.s.db.execute('SELECT text FROM events WHERE conversation_id=?',(digest('slack',slack_id),)).fetchone()[0],envelope)

    def test_new_slack_reply_only_expands_for_a_thread_still_linked_to_source(self):
        parent=self.s.ensure_context('thr_request','Request',SLACK)['work']['id']
        claim=self.s.claim_context('thr_request')
        slack=self.source(body='First message')
        self.s.finish_context(claim,slack)
        self.source(body='Reply: '+PR)
        pr_claim=self.s.claim_context('thr_request')
        self.assertEqual(pr_claim['url'],PR)
        other=self.s.ensure_context('thr_other','Unrelated','Separate work')['work']['id']
        self.s.associate(slack,other,'add')
        self.s.associate(slack,parent,'remove')
        self.source(body='Next reply: https://linear.app/example/issue/ENG-2')
        pr=self.source('github',PR,'Fetched while user changed work')
        self.assertFalse(self.s.finish_context(pr_claim,pr))
        self.assertEqual(self.s._work_ids(pr),[pr])
        self.assertEqual({r['url'] for r in self.s.context_packet('thr_request')['requests']},{SLACK})
        self.assertEqual(self.s.context_packet('thr_other')['requests'],[])

    def test_reopen_existing_slack_work_discovers_links_after_index_upgrade(self):
        thread='thr_legacy'
        self.s.ensure_context(thread,'Request',SLACK)
        claim=self.s.claim_context(thread)
        body=json.dumps({'messages':PR}).replace('/',r'\/')
        cid=self.source(body=body)
        self.s.finish_context(claim,cid)
        # Recreate the old index and the original, already-completed Slack read.
        with self.s.db:
            self.s.db.execute("DELETE FROM source_references WHERE conversation_id=? AND source='github'",(cid,))
            self.s.db.execute('DELETE FROM context_requests WHERE url=?',(PR,))
            self.s.db.execute("DELETE FROM meta WHERE key='slack_reference_index_v2'")
            self.s.db.execute("ALTER TABLE context_requests DROP COLUMN slack_work_id")
        legacy=self.s.export()
        legacy['schema_version']=3
        imported=Store(self.s.path.with_name('imported.db'));self.addCleanup(imported.close)
        imported.import_snapshot(legacy)
        imported.ensure_context(thread,'Request',SLACK,record_prompt=False)
        self.assertEqual(imported.claim_context(thread)['url'],PR)
        reopened=Store(self.s.path);self.addCleanup(reopened.close)
        reopened.ensure_context(thread,'Request',SLACK,record_prompt=False)
        self.assertEqual(reopened.claim_context(thread)['url'],PR)

    def test_discovered_source_scope_and_manual_choices(self):
        work=self.s.ensure_context('thr_request','Request',SLACK)['work']['id']
        slack_claim=self.s.claim_context()
        self.s.finish_context(slack_claim,self.source())
        pr=self.source('github',PR,'Saved work')
        self.s.mutate('work',pr,'focus')
        with patch('source_reads.selected_sources',return_value=[]),patch('source_reads.command') as command:
            hydrate_context(self.s,'thr_request')
            command.assert_not_called()
        self.assertEqual(self.s._work_ids(pr),[pr])
        with self.s.db:self.s.db.execute("UPDATE context_requests SET updated_at=0 WHERE state='error'")
        self.s.finish_context(self.s.claim_context('thr_request'),pr)
        self.assertCountEqual(self.s._work_ids(pr),[pr,work])

    def test_manual_association_survives_new_references(self):
        first=self.s.ensure_context('thr_new','New request','Start here')['work']['id'];other=self.source()
        self.s.associate(digest('bb','thr_new'),other,'add')
        self.s.ensure_context('thr_new','New request',SLACK)
        self.assertEqual(self.s.thread_context('thr_new')['work']['id'],first)
        self.assertEqual(len(self.s.thread_context('thr_new')['associated_work']),2)
    def test_completed_work_is_not_reopened_for_a_repeat(self):
        cid=self.source();self.s.mutate('work',cid,'done')
        p=self.s.ensure_context('thr_new','Repeat cleanup','Do this again '+SLACK)
        self.assertNotEqual(p['work']['id'],cid)
        self.assertEqual(self.s.db.execute('SELECT status FROM work WHERE id=?',(cid,)).fetchone()[0],'done')
    def test_ambiguous_reference_matches_are_candidates_not_silent_merge(self):
        first=self.source();second=self.source('github',PR,'Related request')
        p=self.s.ensure_context('thr_new','Work',PR)
        self.assertEqual(p['work']['id'],digest('bb','thr_new'))
        self.assertEqual(set(p['candidates']),{first,second})
    def test_backfill_reference_preserves_latest_conversation_receipt(self):
        self.s.ensure_context('thr_new','Work','Most recent progress')
        cid=digest('bb','thr_new')
        before=self.s.db.execute('SELECT latest_event FROM conversations WHERE id=?',(cid,)).fetchone()[0]
        self.s.ensure_context('thr_new','Work',SLACK,record_prompt=False)
        self.assertEqual(self.s.db.execute('SELECT latest_event FROM conversations WHERE id=?',(cid,)).fetchone()[0],before)
        self.assertEqual(self.s.context_packet('thr_new')['requests'][0]['url'],SLACK)

    def test_context_failure_and_stale_lease(self):
        self.s.ensure_context('thr_new','Work',SLACK);claim=self.s.claim_context()
        with self.s.db:self.s.db.execute('UPDATE context_requests SET lease_until=0')
        new=self.s.claim_context()
        self.assertFalse(self.s.finish_context(claim,error='old worker'))
        self.assertTrue(self.s.finish_context(new,error='unavailable'))
        self.assertEqual(self.s.context_packet('thr_new')['requests'][0]['state'],'error')
    def test_cross_source_evidence_and_verified_save(self):
        self.commitment();cid=self.source('github',PR,'Review submitted',time.time())
        eid=self.s.db.execute('SELECT latest_event FROM conversations WHERE id=?',(cid,)).fetchone()[0]
        response,_=self.review(self.result(eid));self.assertTrue(response['applied'])
        page=self.s.completion_page();self.assertEqual(page['items'][0]['verdict'],'confirmed_complete')
        self.assertEqual(page['items'][0]['commitment_state'],'open')
        self.s.resolve_completion('o')
        self.assertEqual(self.s.db.execute('SELECT state FROM obligations').fetchone()[0],'fulfilled')
    def test_failed_source_refresh_prevents_confirmation(self):
        self.commitment();cid=self.source('github',PR,'Review submitted',time.time())
        eid=self.s.db.execute('SELECT latest_event FROM conversations WHERE id=?',(cid,)).fetchone()[0]
        self.s.queue_completion('selected',['o'])
        with patch('completion.refresh_reference',side_effect=RuntimeError('offline')),patch('completion.identity',return_value={}),patch('completion.run',return_value=self.result(eid)):
            once(self.s)
        self.assertEqual(self.s.completion_page()['items'][0]['verdict'],'unconfirmed')
        with self.assertRaises(ValueError):self.s.resolve_completion('o')

    def test_partial_completion_remains_open(self):
        self.commitment();cid=self.source('github',PR,'Review submitted',time.time())
        eid=self.s.db.execute('SELECT latest_event FROM conversations WHERE id=?',(cid,)).fetchone()[0]
        self.review(self.result(eid,verdict='partly_complete'))
        with self.assertRaises(ValueError):self.s.resolve_completion('o')
        self.assertEqual(self.s.completion_page()['items'][0]['commitment_state'],'open')

    def test_fabricated_quote_cannot_resolve(self):
        self.commitment();cid=self.source('github',PR,'Review submitted',time.time());eid=self.s.db.execute('SELECT latest_event FROM conversations WHERE id=?',(cid,)).fetchone()[0]
        self.review(self.result(eid,quote='not present'))
        self.assertEqual(self.s.completion_page()['items'][0]['state'],'error')
        with self.assertRaises(ValueError):self.s.resolve_completion('o')
    def test_newer_source_invalidates_confirmation(self):
        self.commitment();cid=self.source('github',PR,'Review submitted',time.time());eid=self.s.db.execute('SELECT latest_event FROM conversations WHERE id=?',(cid,)).fetchone()[0]
        self.review(self.result(eid));self.source('github',PR,'New revision needs another review',time.time())
        with self.assertRaises(ValueError):self.s.resolve_completion('o')
    def test_concurrent_manual_edit_is_never_overwritten(self):
        self.commitment();self.s.queue_completion('selected',['o']);claim=self.s.claim_completion()
        self.s.mutate('obligation','o','cancelled',version=1,value='No longer needed')
        self.assertFalse(self.s.finish_completion(claim,'confirmed_complete','Done',[],'x')['applied'])
        self.assertEqual(self.s.db.execute('SELECT state FROM obligations').fetchone()[0],'cancelled')
    def test_full_inventory_exceeds_dashboard_limit(self):
        cid=self.commitment();o=dict(self.s.db.execute('SELECT * FROM obligations').fetchone())
        with self.s.db:
            for n in range(632):
                row={**o,'id':'o'+str(n)};self.s.db.execute('INSERT INTO obligations('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',list(row.values()))
        self.assertEqual(self.s.queue_completion('unseen')['queued'],633)
        self.assertEqual(self.s.completion_page()['total'],633)
        self.assertEqual(len(self.s.completion_page()['items']),50)
        self.assertTrue(self.s.completion_page()['has_more'])
    def test_targeted_read_preserves_collector_coverage(self):
        self.s.ingest('slack',[],checkpoint={'through':42},scope='regular scan')
        before=dict(self.s.db.execute("SELECT * FROM sources WHERE name='slack'").fetchone())
        with patch('source_reads.read_reference',return_value={'external_id':'C123:1794900000.000000','title':'Source','text':'Text','url':SLACK}):refresh_reference(self.s,SLACK)
        self.assertEqual(dict(self.s.db.execute("SELECT * FROM sources WHERE name='slack'").fetchone()),before)
    def test_new_receipt_requeues_affected_assessment(self):
        self.commitment();self.s.queue_completion('selected',['o'])
        with self.s.db:
            self.s._put_record('completion_auto_enabled',True)
            self.s.db.execute("UPDATE completion_reviews SET state='done',verdict='unconfirmed'")
        self.source('github',PR,'New evidence',time.time())
        self.assertEqual(self.s.completion_page()['items'][0]['state'],'queued')

class ReferenceTests(unittest.TestCase):
    def test_slack_reply_normalizes_to_thread_and_workspace(self):
        a=reference(SLACK+'?thread_ts=1794800000.000001&cid=C123')
        self.assertEqual(a['stamp'],'1794800000.000001')
        self.assertNotEqual(a['key'],reference(SLACK.replace('example.','different.'))['key'])
    def test_repo_and_workspace_are_part_of_identity(self):
        self.assertNotEqual(reference(PR)['key'],reference(PR.replace('/example/','/other/'))['key'])
        self.assertNotEqual(reference('https://linear.app/a/issue/ENG-1')['key'],reference('https://linear.app/b/issue/ENG-1')['key'])
    def test_untrusted_urls_do_not_become_fetch_targets(self):
        for url in ['http://127.0.0.1/','https://github.com.evil.test/a/b/pull/1','https://user:pass@github.com/a/b/pull/1','file:///tmp/private']:
            self.assertIsNone(reference(url))
        self.assertEqual(len(references('See '+PR+'.')),1)
