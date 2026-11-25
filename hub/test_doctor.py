from test_support import isolate_storage
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
import doctor
import settings


class DoctorTests(unittest.TestCase):
    """Synthetic state only: no live CLIs, BB vault, launchd or network."""

    def setUp(self):
        isolate_storage(self)
        self.state = settings.STATE
        self.state.chmod(0o700)
        self.home = Path(tempfile.mkdtemp())
        self.calls = []

    def write(self, records):
        (self.state / 'settings.json').write_text(json.dumps(records))

    def run_checks(self, codes=None, installed=('gh', 'claude')):
        codes = codes or {}
        def run(args, timeout=20):
            self.calls.append(args)
            return codes.get(args[0] if args[0] != 'launchctl' else 'launchctl', 0)
        which = lambda name: '/usr/bin/' + name if name in installed else None
        with patch.object(doctor.sys, 'platform', 'darwin'):
            return {c['name']: c for c in doctor.run_checks(self.state, run=run, which=which, home=self.home)}

    def test_fresh_install_reports_missing_config_without_failing_hard(self):
        checks = self.run_checks()
        self.assertEqual(checks['Config']['level'], 'fail')
        self.assertIn('configure.py set config', checks['Config']['fix'])
        self.assertEqual(checks['Job: analyze']['level'], 'warn')

    def test_example_identity_and_missing_analyzer_are_flagged(self):
        self.write({'config': {'identity': {'name': 'Your Name', 'email': 'you@example.com', 'timezone': 'UTC'}, 'sources': []}})
        checks = self.run_checks()
        self.assertEqual(checks['Identity']['level'], 'warn')
        self.assertEqual(checks['Sources']['level'], 'info')
        self.assertEqual(checks['Analyzer']['level'], 'fail')
        self.assertEqual(checks['BB context']['level'], 'warn')

    def test_configured_sources_and_sign_ins(self):
        self.write({
            'config': {'identity': {'name': 'Ada', 'email': 'ada@corp.test', 'timezone': 'UTC'},
                       'sources': ['github', 'linear', 'slack']},
            'analyzer': {'provider': 'claude'}, 'policy': {'max_analyses_per_day': 10},
            'bb_context': {'project': {'id': 'proj_fixture'}},
        })
        checks = self.run_checks(codes={'gh': 1, 'launchctl': 1})
        self.assertEqual(checks['Identity']['level'], 'ok')
        self.assertEqual(checks['Source: github']['detail'], 'gh signed out')
        self.assertEqual(checks['Source: linear']['level'], 'ok')
        self.assertEqual(checks['Source: slack']['level'], 'fail')
        self.assertIn('slack_tokens', checks['Source: slack']['detail'])
        self.assertEqual(checks['Analyzer']['level'], 'ok')
        self.assertEqual(checks['Job: collect']['detail'], 'not installed')
        # Status commands only: nothing signs in, installs or loads.
        for args in self.calls:
            self.assertIn(args[1:3], (['auth', 'status'], ['linear', 'status'], ['print', args[2]]))

    def test_job_from_another_clone_is_not_counted_as_installed(self):
        import plistlib
        agents = self.home / 'Library/LaunchAgents'
        agents.mkdir(parents=True)
        for label, script in (('analyze', '/elsewhere/hub/worker.py'), ('collect', str(doctor.ROOT / 'collect.py'))):
            with (agents / ('local.work-hub.%s.plist' % label)).open('wb') as f:
                plistlib.dump({'Label': label, 'ProgramArguments': ['/usr/bin/python3', script]}, f)
        self.write({'config': {'identity': {'name': 'Ada', 'email': 'ada@corp.test', 'timezone': 'UTC'}, 'sources': ['github']}})
        checks = self.run_checks()
        self.assertEqual(checks['Job: analyze']['detail'], 'installed for a different Work Hub folder')
        self.assertEqual(checks['Job: collect']['detail'], 'installed and loaded')

    def test_ledger_coverage_is_read_only(self):
        self.write({'config': {'identity': {'name': 'Ada', 'email': 'ada@corp.test', 'timezone': 'UTC'}, 'sources': ['github']}})
        db = sqlite3.connect(self.state / 'work.db')
        db.execute('CREATE TABLE sources(name TEXT PRIMARY KEY, state TEXT, scope TEXT, checkpoint TEXT, checked_at REAL, last_success REAL, error TEXT)')
        db.execute("CREATE TABLE events(id TEXT, analysis_state TEXT)")
        db.execute("INSERT INTO sources(name,state,last_success) VALUES('bb','ok',?)", (time.time() - 60,))
        db.commit(); db.close()
        before = (self.state / 'work.db').stat().st_mtime_ns
        checks = self.run_checks()
        self.assertEqual(checks['Coverage: bb']['level'], 'ok')
        self.assertEqual(checks['Coverage: github']['level'], 'warn')
        self.assertEqual((self.state / 'work.db').stat().st_mtime_ns, before)

    def test_report_never_contains_record_values(self):
        self.write({'config': {'identity': {'name': 'Ada', 'email': 'ada@corp.test', 'timezone': 'UTC'}, 'sources': ['slack'],
                               'slack': {'team_id': 'T123', 'workspace_url': 'https://corp.slack.com'}},
                    'slack_client': {'client_id': 'id-secret-value', 'client_secret': 'secret-value'},
                    'slack_tokens': {'access_token': 'token-secret-value'}})
        output = doctor.text(list(self.run_checks().values()))
        self.assertIn('Source: slack', output)
        for value in ('secret-value', 'ada@corp.test'):
            self.assertNotIn(value, output)


if __name__ == '__main__':
    unittest.main()
