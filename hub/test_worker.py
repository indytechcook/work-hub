import io
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from store import Store
from storage import Store as SettingsStore
import provider_usage
from provider_usage import codex_status, headroom, record_claude_event
from worker import reserve, run
from test_support import isolate_storage


def codex_result(used, resets=2000, reached=None, allowed=True):
    return {'ordinaryUsageAllowed': allowed, 'rateLimits': {
        'primary': {'usedPercent': used, 'windowDurationMins': 10080, 'resetsAt': resets},
        'secondary': None, 'rateLimitReachedType': reached, 'spendControlReached': False}}


class Ledger(unittest.TestCase):
    def setUp(self):
        isolate_storage(self)
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name)
        self.s = Store(self.state / 'work.db')
        self.s.ingest('gmail', [dict(external_id=f'fixture-{i}', title='Fixture', text='Fixture', version='1') for i in range(5)], scope='test')

    def tearDown(self):
        self.s.close()
        self.temp.cleanup()


class ReserveTests(Ledger):
    def test_reservation_has_no_daily_cap(self):
        for _ in range(1000):
            self.assertEqual(reserve(self.s, time.time()), {'reserved': True})
        day = time.strftime('%Y-%m-%d', time.gmtime())
        self.assertEqual(self.s._record('analysis_runs:' + day, 0), 1000)

    def test_idle_without_pending_work(self):
        with self.s.db:
            self.s.db.execute("UPDATE events SET analysis_state='done'")
        self.assertEqual(reserve(self.s, time.time()), {'idle': True})


class CodexStatusTests(unittest.TestCase):
    def test_headroom_below_threshold(self):
        self.assertTrue(codex_status(codex_result(42), 90, 1000)['allowed'])

    def test_pauses_at_threshold_until_window_reset(self):
        status = codex_status(codex_result(90, resets=5000), 90, 1000)
        self.assertEqual((status['allowed'], status['resume_at']), (False, 5000))

    def test_reached_limit_pauses_even_below_threshold(self):
        self.assertFalse(codex_status(codex_result(10, reached='primary'), 90, 1000)['allowed'])
        self.assertFalse(codex_status(codex_result(10, allowed=False), 90, 1000)['allowed'])


class HeadroomTests(Ledger):
    def setUp(self):
        super().setUp()
        SettingsStore(self.state).write('analyzer', {'provider': 'codex', 'executable': 'python3'})
        self.reads = 0

    def read(self, used):
        def reader(executable, state):
            self.reads += 1
            return codex_result(used, resets=10_000)
        return reader

    def test_codex_reading_is_cached(self):
        self.assertTrue(headroom(self.s, self.state, {}, now=1000, read_codex=self.read(5))['allowed'])
        headroom(self.s, self.state, {}, now=1100, read_codex=self.read(5))
        self.assertEqual(self.reads, 1)
        headroom(self.s, self.state, {}, now=1000 + provider_usage.CACHE_SECONDS + 1, read_codex=self.read(5))
        self.assertEqual(self.reads, 2)

    def test_paused_codex_waits_for_reset_without_rereading(self):
        self.assertFalse(headroom(self.s, self.state, {}, now=1000, read_codex=self.read(95))['allowed'])
        self.assertFalse(headroom(self.s, self.state, {}, now=9000, read_codex=self.read(1))['allowed'])
        self.assertEqual(self.reads, 1)
        self.assertTrue(headroom(self.s, self.state, {}, now=10_001, read_codex=self.read(1))['allowed'])

    def test_threshold_is_configurable(self):
        self.assertFalse(headroom(self.s, self.state, {'max_provider_used_percent': 50}, now=1000, read_codex=self.read(60))['allowed'])
        with self.assertRaises(ValueError):
            headroom(self.s, self.state, {'max_provider_used_percent': 0}, now=1000, read_codex=self.read(1))

    def test_unreadable_quota_allows_cautious_work(self):
        def broken(executable, state):
            raise RuntimeError('unavailable')
        status = headroom(self.s, self.state, {}, now=1000, read_codex=broken)
        self.assertEqual((status['allowed'], status['known']), (True, False))

    def test_claude_rejection_pauses_until_reset(self):
        SettingsStore(self.state).write('analyzer', {'provider': 'claude', 'executable': 'python3'})
        self.assertTrue(headroom(self.s, self.state, {}, now=1000)['allowed'])
        record_claude_event(self.s, {'status': 'rejected', 'resetsAt': 5000}, now=1100)
        self.assertFalse(headroom(self.s, self.state, {}, now=4000)['allowed'])
        self.assertTrue(headroom(self.s, self.state, {}, now=5001)['allowed'])


class RunTests(Ledger):
    def setUp(self):
        super().setUp()
        self.clock = 0.0
        self.calls = 0

    def now(self):
        self.clock += 1
        return self.clock

    def analyze(self, result):
        def step(store):
            self.calls += 1
            with store.db:
                store.db.execute("UPDATE events SET analysis_state='done' WHERE id=(SELECT id FROM events WHERE analysis_state='pending' LIMIT 1)")
            return result
        return step

    def go(self, gate, result=None):
        out = io.StringIO()
        with redirect_stdout(out):
            run(self.s, self.state, now=self.now, analyze=self.analyze(result or {'ok': True}), check=lambda s: {'idle': True}, gate=gate)
        return out.getvalue()

    def test_drains_backlog_while_provider_allows(self):
        self.go(lambda *a: {'allowed': True, 'known': True})
        self.assertEqual(self.calls, 5)

    def test_stops_when_provider_limit_reached(self):
        output = self.go(lambda *a: {'allowed': False, 'resume_at': 5000, 'reason': 'provider limit reached'})
        self.assertEqual(self.calls, 0)
        self.assertIn('provider_limit', output)

    def test_unknown_quota_makes_one_call(self):
        self.go(lambda *a: {'allowed': True, 'known': False})
        self.assertEqual(self.calls, 1)

    def test_provider_failure_stops_and_forgets_quota(self):
        with self.s.db:
            self.s._put_record('provider_usage', {'provider': 'codex', 'allowed': True})
        self.go(lambda *a: {'allowed': True, 'known': True}, {'error': 'RuntimeError'})
        self.assertEqual(self.calls, 1)
        self.assertIsNone(self.s._record('provider_usage', None))

    def test_item_errors_do_not_stop_the_run_until_three_in_a_row(self):
        self.go(lambda *a: {'allowed': True, 'known': True}, {'error': 'ValueError'})
        self.assertEqual(self.calls, 3)


if __name__ == '__main__':
    unittest.main()
