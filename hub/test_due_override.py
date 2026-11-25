from test_support import isolate_storage
import tempfile
import time
import unittest
from pathlib import Path
from store import Store

QUOTE = 'I will send the draft tomorrow.'


class DueOverrideTests(unittest.TestCase):
    def setUp(self):
        isolate_storage(self)
        self.temp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.temp.name) / 'work.db')

    def tearDown(self):
        self.s.close()
        self.temp.cleanup()

    def analyze(self, version, due):
        self.s.ingest('gmail', [dict(external_id='g', title='Draft', version=version, text=QUOTE, occurred_at=time.time())])
        c = self.s.claim_analysis()
        self.s.apply_analysis(c['id'], c['lease_token'], dict(summary='s', next_action='n', decisions=[], obligations=[
            dict(existing_id=None, title='Send draft', owner='Alex', direction='i_owe', due_at=due, state='open',
                 quote=QUOTE, confidence=.99, explicit_resolution=False, work_id=None)]))
        return self.s.db.execute('SELECT id,version,due_at FROM obligations').fetchone()

    def test_your_date_wins_over_reanalysis(self):
        oid, version, _ = self.analyze('1', 1000.0)
        self.s.mutate('obligation', oid, 'due', version, '2000000000')
        self.assertEqual(self.analyze('2', 1000.0)[2], 2000000000.0)

    def test_clear_date_and_stale_version(self):
        oid, version, _ = self.analyze('1', 1000.0)
        self.s.mutate('obligation', oid, 'due', version, '')
        self.assertIsNone(self.s.db.execute('SELECT due_at FROM obligations').fetchone()[0])
        with self.assertRaises(ValueError):
            self.s.mutate('obligation', oid, 'due', version, '2000000000')
        with self.assertRaises(ValueError):
            self.s.mutate('obligation', oid, 'due', version + 1, 'soon')


if __name__ == '__main__':
    unittest.main()
