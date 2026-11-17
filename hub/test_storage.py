import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from configure import legacy_records
from storage import Store, StorageError, decode


class StorageTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.state = Path(folder.name)
        env = patch.dict(os.environ, {}, clear=True)
        env.start(); self.addCleanup(env.stop)
        self.rpc = patch.object(Store, 'rpc', side_effect=AssertionError('Unexpected BB call'))
        self.rpc.start(); self.addCleanup(self.rpc.stop)

    def test_explicit_json_never_calls_bb_and_atomically_preserves_other_records(self):
        os.environ['WORK_HUB_STORAGE'] = 'json'
        store = Store(self.state)
        self.assertIsNone(store.read('config'))
        store.write('config', {'sources': []})
        store.write('slack_tokens', {'access_token': 'synthetic'})
        self.assertEqual(store.read('config'), {'sources': []})
        self.assertEqual(store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.state.glob('.work-hub-*')), [])

    def test_environment_overrides_json_overrides_bb(self):
        path = self.state / 'override.json'
        path.write_text(json.dumps({'config': {'sources': ['github']}}))
        os.environ['WORK_HUB_CONFIG_FILE'] = str(path)
        os.environ['WORK_HUB_CONFIG_JSON'] = '{"sources":[]}'
        store = Store(self.state)
        self.assertEqual(store.read('config'), {'sources': []})
        del os.environ['WORK_HUB_CONFIG_JSON']
        self.assertEqual(store.read('config'), {'sources': ['github']})
        self.assertEqual(store.origin('google_tokens'), 'bb')

    def test_json_override_refresh_writes_back_to_same_bundle(self):
        path = self.state / 'override.json'
        path.write_text('{"slack_tokens":{"access_token":"old"}}')
        os.environ['WORK_HUB_CONFIG_FILE'] = str(path)
        store = Store(self.state)
        store.write('slack_tokens', {'access_token': 'new'})
        self.assertEqual(json.loads(path.read_text())['slack_tokens']['access_token'], 'new')

    def test_environment_is_read_only(self):
        os.environ['WORK_HUB_GOOGLE_TOKENS_JSON'] = '{}'
        with self.assertRaises(StorageError):
            Store(self.state).write('google_tokens', {'token': 'rotated'})

    def test_invalid_explicit_environment_never_falls_back(self):
        for value in ('', '[]', 'synthetic-secret', '{"a":NaN}'):
            os.environ['WORK_HUB_CONFIG_JSON'] = value
            with self.assertRaises(StorageError) as error:
                Store(self.state).read('config')
            self.assertNotIn('synthetic-secret', str(error.exception))

    def test_corrupt_bundle_cannot_be_overwritten(self):
        os.environ['WORK_HUB_STORAGE'] = 'json'
        store = Store(self.state)
        store.path.write_text('corrupt')
        with self.assertRaises(StorageError):
            store.write('config', {})
        self.assertEqual(store.path.read_text(), 'corrupt')

    def test_missing_explicit_override_file_does_not_select_another_account(self):
        os.environ['WORK_HUB_CONFIG_FILE'] = str(self.state / 'missing.json')
        with self.assertRaises(StorageError):Store(self.state).read('config')

    def test_default_bb_read_write_and_missing_are_distinct(self):
        values = {}
        def rpc(method, payload):
            if method == 'env_list':
                return {'variables': [{'name': k} for k in values]}
            if method == 'env_get_value':
                return {'value': values[payload['name']]}
            self.assertEqual(method, 'env_save')
            values[payload['name']] = payload['value']
            return {'success': True}
        with patch.object(Store, 'rpc', side_effect=rpc):
            store = Store(self.state)
            self.assertIsNone(store.read('google_tokens'))
            store.write('google_tokens', {'token': 'synthetic'})
            self.assertEqual(store.read('google_tokens'), {'token': 'synthetic'})
            self.assertIn('WORK_HUB_DEFAULT_GOOGLE_TOKENS', values)
        self.assertEqual(list(self.state.iterdir()), [])

    def test_unavailable_bb_never_creates_plaintext_fallback(self):
        with patch.object(Store, 'rpc', side_effect=StorageError('Unavailable')):
            with self.assertRaises(StorageError):Store(self.state).write('config', {})
        self.assertEqual(list(self.state.iterdir()), [])

    def test_explicit_profile_and_invalid_names(self):
        os.environ['WORK_HUB_PROFILE'] = 'trial_two'
        self.assertEqual(Store(self.state).prefix, 'WORK_HUB_TRIAL_TWO_')
        for value in ('TRIAL', '../escape', '', 'a-b'):
            os.environ['WORK_HUB_PROFILE'] = value
            with self.assertRaises(StorageError):Store(self.state)

    def test_rpc_uses_stdin_and_sanitizes_failed_process_output(self):
        self.rpc.stop()
        def run(args, **kwargs):
            self.assertNotIn('synthetic-secret', ' '.join(args))
            self.assertEqual(json.loads(kwargs['input']), {'value': 'synthetic-secret'})
            self.assertTrue(kwargs['capture_output'])
            return subprocess.CompletedProcess(args, 1, 'synthetic-secret', 'synthetic-secret')
        with patch('storage.subprocess.run', side_effect=run):
            with self.assertRaises(StorageError) as error:
                Store(self.state).rpc('env_save', {'value': 'synthetic-secret'})
            self.assertNotIn('synthetic-secret', str(error.exception))

    def test_migration_preserves_legacy_expiry(self):
        folder = self.state / 'hub' / '.state' / 'betterstack'
        folder.mkdir(parents=True)
        path = folder / 'tokens.json'
        path.write_text('{"access_token":"synthetic","expires_in":7200}')
        os.utime(path, (1000, 1000))
        self.assertEqual(legacy_records(self.state)['betterstack_tokens']['_expires_at'], 8200)
        self.assertTrue(path.exists())

    def test_reject_unknown_record_and_oversize_value(self):
        os.environ['WORK_HUB_STORAGE'] = 'json'
        for name, value in (('unknown', {}), ('config', {'text': 'a' * 65536})):
            with self.assertRaises(StorageError):Store(self.state).write(name, value)
