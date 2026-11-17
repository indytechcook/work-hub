import json
import unittest
from source_preview import slack_preview

MESSAGE='''=== THREAD PARENT MESSAGE ===
From: Sam Example (U12345)
Time: 2026-11-17 10:00:00 EST
Message TS: 1794930000.123456
Please review <https://github.com/example/repo/pull/12|PR #12> with <@U678|Alex> &amp; confirm.
Reactions: eyes (1)

=== THREAD REPLIES (2 total) ===
--- Reply 1 of 2 ---
From: Alex (U678)
Time: 2026-11-17 10:01:00 EST
Message TS: 1794930060.123456
Private later reply
'''

class SourcePreviewTests(unittest.TestCase):
    def test_parent_excerpt_has_author_time_and_reply_count_without_envelope(self):
        p=slack_preview(MESSAGE)
        self.assertEqual(p,{'text':'Please review PR #12 with @Alex & confirm.','author':'Sam Example','at':1794930000.123456,'replies':2})
    def test_targeted_json_read_has_same_preview(self):
        self.assertEqual(slack_preview(json.dumps({'messages':MESSAGE,'pagination_info':'none'})),slack_preview(MESSAGE))
    def test_unknown_or_missing_shape_does_not_show_raw_connector_response(self):
        for body in (None,'unavailable','{"error":"private response"}',json.dumps({'messages':[]})):
            self.assertIsNone(slack_preview(body))
    def test_long_body_is_bounded_and_replies_are_not_assumed(self):
        p=slack_preview(MESSAGE.split('Please review')[0]+'x'*900)
        self.assertEqual(len(p['text']),421);self.assertTrue(p['text'].endswith('…'));self.assertIsNone(p['replies'])
