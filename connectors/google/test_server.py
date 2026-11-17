import base64
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from mcp.server.fastmcp.exceptions import ToolError
import server


class GoogleReadTests(unittest.TestCase):
    def test_empty_page_can_still_have_more_results(self):
        result = server.page({"nextPageToken": "next"}, "files")
        self.assertEqual(result["items"], [])
        self.assertTrue(result["has_more"])
        self.assertEqual(result["next_page_token"], "next")

    def test_last_page_has_no_cursor(self):
        self.assertFalse(server.page({"files": [{"id": "1"}]}, "files")["has_more"])

    def test_nested_plain_text_is_decoded(self):
        text = "Please follow up next week."
        part = {"parts": [{"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")}}]}
        self.assertEqual(server.plain_parts(part), [text])

    def test_html_is_not_presented_as_complete_plain_text(self):
        self.assertEqual(server.plain_parts({"mimeType": "text/html", "body": {"data": "SGk"}}), [])

    def test_naive_calendar_times_are_rejected(self):
        with self.assertRaises(ToolError):
            server.time_window(datetime(2026, 1, 1), datetime(2026, 1, 2))

    def test_reversed_calendar_window_is_rejected(self):
        with self.assertRaises(ToolError):
            server.time_window(datetime(2026, 1, 2, tzinfo=timezone.utc), datetime(2026, 1, 1, tzinfo=timezone.utc))

    @patch("server.google_get")
    def test_calendar_id_cannot_inject_path(self, get):
        get.return_value = {}
        server.google_calendar_events(datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 2, tzinfo=timezone.utc), "../other?x=y")
        self.assertIn("..%2Fother%3Fx%3Dy/events", get.call_args.args[0])
        self.assertEqual(get.call_args.args[1]["showDeleted"], "true")

    @patch("server.google_get")
    def test_drive_incomplete_search_is_preserved(self, get):
        get.return_value = {"incompleteSearch": True, "nextPageToken": "next"}
        result = server.google_drive_search(limit=1, ids_only=True)
        self.assertTrue(result["incomplete_search"])
        self.assertTrue(result["has_more"])
        self.assertEqual(get.call_args.args[1]["pageSize"], 1)
        self.assertNotIn("name", get.call_args.args[1]["fields"])

    @patch("server.google_get")
    def test_thread_truncation_and_remaining_messages_are_visible(self, get):
        body = base64.urlsafe_b64encode(b"x" * 12001).decode()
        get.return_value = {"messages": [{"id": "1", "payload": {"mimeType": "text/plain", "body": {"data": body}}}, {"id": "2"}]}
        result = server.google_gmail_read_thread("abc", limit=1)
        self.assertTrue(result["items"][0]["text_truncated"])
        self.assertEqual(len(result["items"][0]["text"]), 12000)
        self.assertEqual(result["next_message_offset"], 1)
        self.assertTrue(result["source_content_is_untrusted"])

    @patch("server.google_get")
    def test_gmail_pagination_preserves_query(self, get):
        get.return_value = {"messages": [{"id": "abc"}]}
        server.google_gmail_search("in:sent newer_than:7d", limit=2, page_token="cursor")
        self.assertEqual(get.call_args.args[1]["q"], "in:sent newer_than:7d")
        self.assertEqual(get.call_args.args[1]["pageToken"], "cursor")
        self.assertEqual(get.call_args.args[1]["maxResults"], 2)


if __name__ == "__main__":
    unittest.main()
