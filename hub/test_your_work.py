from test_support import isolate_storage
import tempfile
import time
import unittest
from pathlib import Path
from store import Store


class YourWorkTests(unittest.TestCase):
    def setUp(self):
        isolate_storage(self)
        self.temp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.temp.name) / 'work.db')
        self.s.ingest('gmail', [dict(external_id='g', title='Thread', text='x')])
        cid = self.s.db.execute('SELECT id FROM conversations').fetchone()[0]
        now = time.time()
        rows = [
            ('a', 'Overdue thing', 'Alex', 'i_owe', now - 3 * 86400, 'open'),
            ('b', 'Old overdue', 'Alex', 'i_owe', now - 40 * 86400, 'open'),
            ('c', 'Undated', 'Alex', 'i_owe', None, 'open'),
            ('d', 'Unclear owner', 'Unclear', 'unclear', None, 'needs_review'),
            ('e', 'Send the file', 'Dana', 'they_owe', None, 'open'),
            ('f', 'Approve it', 'Dana Reyes', 'they_owe', None, 'open'),
            ('g', 'Fix the bug', 'Assistant', 'they_owe', None, 'open'),
            ('h', 'Finished', 'Alex', 'i_owe', now - 86400, 'fulfilled'),
        ]
        for oid, title, owner, direction, due, state in rows:
            self.s.db.execute('INSERT INTO obligations(id,work_id,conversation_id,evidence_event,evidence_quote,title,owner,direction,due_at,state,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                              (oid, cid, cid, 'e', 'q', title, owner, direction, due, state, now))
        self.s.db.commit()

    def tearDown(self):
        self.s.close()
        self.temp.cleanup()

    def test_counts_and_people(self):
        y = self.s.your_work()
        owe = y['owe']
        self.assertEqual((owe['total'], owe['overdue'], owe['noDate'], owe['confirm']), (3, 2, 1, 1))
        self.assertEqual(owe['overdueBuckets'], [0, 1, 0, 1])
        self.assertEqual([r['title'] for r in owe['overdueQueue']], ['Overdue thing', 'Old overdue'])
        self.assertEqual([(p['name'], p['count']) for p in y['waiting']['people']], [('Dana Reyes', 2)])
        self.assertEqual(y['waiting']['total'], 2)


if __name__ == '__main__':
    unittest.main()
