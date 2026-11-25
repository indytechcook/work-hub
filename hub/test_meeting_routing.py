from test_support import isolate_storage
import tempfile
import time
import unittest
from pathlib import Path
from store import Store, digest

NOTES = 'Alex will rotate the API key. Alex will draft the Q1 plan.'


class MeetingRoutingTests(unittest.TestCase):
    def setUp(self):
        isolate_storage(self)
        self.temp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.temp.name) / 'work.db')
        # Existing work the meeting talks about.
        self.s.ingest('linear', [dict(external_id='KEY-1', title='KEY-1 Rotate the API key', text='Rotate the key.')])
        self.key_work = digest('linear', 'KEY-1')
        self.s.db.execute("UPDATE events SET analysis_state='done'")
        self.s.db.commit()
        self.s.ingest('meetings', [dict(external_id='m1', title='Weekly sync', text=NOTES, occurred_at=time.time())])
        self.meeting = digest('meetings', 'm1')

    def tearDown(self):
        self.s.close()
        self.temp.cleanup()

    def ob(self, title, quote, work_id):
        return dict(existing_id=None, title=title, owner='Alex', direction='i_owe', due_at=None, state='open',
                    quote=quote, confidence=.99, explicit_resolution=False, work_id=work_id)

    def analyze(self, rotate_to):
        claim = self.s.claim_analysis()
        self.assertEqual(claim['source'], 'meetings')
        self.assertIn(self.key_work, {c['id'] for c in claim['work_candidates']})
        self.assertNotIn(self.meeting, {c['id'] for c in claim['work_candidates']})
        return self.s.apply_analysis(claim['id'], claim['lease_token'], dict(
            summary='Sync', next_action='Follow up', decisions=[], obligations=[
                self.ob('Rotate the API key', 'Alex will rotate the API key.', rotate_to),
                self.ob('Draft the Q1 plan', 'Alex will draft the Q1 plan.', None),
            ]))

    def titles(self, work_id):
        return sorted(o['title'] for o in self.s.work_detail(work_id)['obligations'])

    def test_commitment_is_filed_under_the_work_it_continues(self):
        self.assertTrue(self.analyze(self.key_work)['applied'])
        self.assertEqual(self.titles(self.key_work), ['Rotate the API key'])
        self.assertEqual(self.titles(self.meeting), ['Draft the Q1 plan'])
        # The meeting is listed among the work's conversations.
        self.assertIn(self.meeting, {c['id'] for c in self.s.work_detail(self.key_work)['work']['conversations']})

    def test_route_survives_later_scans(self):
        self.analyze(self.key_work)
        self.s.ingest('linear', [dict(external_id='KEY-2', title='Unrelated', text='x')])
        self.assertEqual(self.titles(self.key_work), ['Rotate the API key'])

    def test_unknown_or_meeting_work_is_not_a_route(self):
        self.analyze('not-a-work-id')
        self.assertEqual(self.titles(self.meeting), ['Draft the Q1 plan', 'Rotate the API key'])
        self.assertEqual(self.titles(self.key_work), [])
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM obligation_routes').fetchone()[0], 0)

    def test_other_sources_get_no_candidates(self):
        self.s.ingest('gmail', [dict(external_id='g1', title='Mail', text='Alex will reply.', occurred_at=time.time() + 5)])
        sources = []
        while (claim := self.s.claim_analysis()):
            sources.append(claim['source'])
            if claim['source'] != 'meetings':
                self.assertNotIn('work_candidates', claim)
            self.s.analysis_failed(claim['id'], claim['lease_token'], 'skip')
            self.s.db.execute("UPDATE events SET retry_at=? WHERE id=?", (time.time() + 9999, claim['id']))
            self.s.db.commit()
        self.assertIn('gmail', sources)


if __name__ == '__main__':
    unittest.main()
