import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, Mock
from test_support import isolate_storage
from storage import Store, StorageError
import slack_auth


class SlackStorageTests(unittest.TestCase):
    def setUp(self):
        isolate_storage(self)
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.state = Path(folder.name)
        replacement = patch.object(slack_auth, 'STATE', self.state)
        replacement.start(); self.addCleanup(replacement.stop)
        self.store = Store(self.state)
        self.store.write('slack_client', {'client_id': 'synthetic', 'client_secret': 'synthetic'})
        self.token = {'access_token': 'expired', 'refresh_token': 'keep-refresh',
                      'expires_at': time.time() - 1, 'scope': ','.join(slack_auth.SCOPES)}
        self.store.write('slack_tokens', self.token)

    def test_refresh_preserves_omitted_refresh_token_and_scope(self):
        response = Mock(status_code=200)
        response.json.return_value = {'ok': True, 'access_token': 'new', 'expires_in': 7200}
        with patch.object(slack_auth.requests, 'post', return_value=response):
            self.assertEqual(slack_auth.access_token(), 'new')
        saved = self.store.read('slack_tokens')
        self.assertEqual(saved['refresh_token'], 'keep-refresh')
        self.assertEqual(saved['scope'], self.token['scope'])
        self.assertGreater(saved['expires_at'], time.time())

    def test_environment_override_blocks_refresh_before_network(self):
        with patch.dict('os.environ', {'WORK_HUB_SLACK_TOKENS_JSON': json.dumps(self.token)}), patch.object(slack_auth.requests, 'post') as post:
            with self.assertRaises(StorageError):slack_auth.access_token()
            post.assert_not_called()

    def test_failed_refresh_preserves_original_record(self):
        with patch.object(slack_auth.requests, 'post', return_value=Mock(status_code=503)):
            with self.assertRaises(RuntimeError):slack_auth.access_token()
        self.assertEqual(self.store.read('slack_tokens'), self.token)
