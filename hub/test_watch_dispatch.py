import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dispatch import dispatch, job_id, title
from store import Store, digest
from test_support import isolate_storage
from watch import health, register_watch, replies


class NativeJobTests(unittest.TestCase):
    def setUp(self):
        isolate_storage(self)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = Store(Path(folder.name) / "fixture.db")
        self.addCleanup(self.store.close)

    @patch("watch.selected_sources", return_value=["gmail"])
    def test_health_needs_persistence_clears_and_can_recur(self, _):
        s = self.store
        self.assertFalse(health(s, now=2000))
        self.assertFalse(health(s, now=2001))
        self.assertEqual(health(s, now=2900), ["gmail"])
        first = s.attention()[0]["token"]
        s.acknowledge_attention([first])
        self.assertFalse(health(s, now=3800))
        self.assertTrue(s.attention()[0]["seen"])
        s.ingest("gmail", [])
        self.assertEqual(health(s, now=3801), ["gmail"])
        self.assertFalse(s.attention())
        s.source_failure("gmail", "transient")
        health(s, now=4000)
        health(s, now=4900)
        self.assertNotEqual(s.attention()[0]["token"], first)

    @patch(
        "watch.identity", return_value={"name": "Alex", "email": "alex@example.test"}
    )
    @patch("watch.selected_sources", return_value=["gmail"])
    def test_watch_uses_sent_message_and_ignores_own_reply(self, *_):
        url = "https://mail.google.com/mail/u/0/#all/abc"
        s = self.store
        s.ingest(
            "gmail",
            [
                {
                    "external_id": "abc",
                    "title": "Question",
                    "text": "Fixture",
                    "url": url,
                }
            ],
        )
        cid = digest("gmail", "abc")
        register_watch(s, url, "a1", cid)
        register_watch(s, url, "a1", cid)
        records = [("00", 1, "sam"), ("a1", 2, "alex"), ("02", 3, "alex")]
        with (
            patch("watch.read_target", return_value=(cid, records)),
            patch("watch.time.time", return_value=10000),
        ):
            self.assertEqual(replies(s)["arrivals"], 0)
        records.append(("a2", 4, "sam"))
        with (
            patch("watch.read_target", return_value=(cid, records)),
            patch("watch.time.time", return_value=12000),
        ):
            self.assertEqual(replies(s)["arrivals"], 1)
            self.assertEqual(replies(s)["checked"], 0)
        arrival = next(a for a in s.attention() if a["kind"] == "reply")
        self.assertIn(cid, arrival["work_ids"])

    def test_dispatch_skips_backlog_and_recovers_spawn_receipt(self):
        s = self.store
        item = {
            "kind": "betterstack_triage",
            "key": "1/2",
            "revision": "0",
            "source": "betterstack",
            "external": "2",
        }
        s.ingest(
            "betterstack", [{"external_id": "2", "title": "Error", "text": "Fixture"}]
        )
        with (
            patch("dispatch.Settings") as settings,
            patch("dispatch.selected_sources", return_value=["betterstack"]),
            patch("dispatch.betterstack", return_value={"application_id": 1}),
            patch("dispatch.candidates", return_value=[item]),
            patch("dispatch.command") as cmd,
        ):
            settings.return_value.read.side_effect = lambda key: {
                "bb_context": {"project": {"id": "proj_x"}},
                "morning": {},
            }[key]
            cmd.return_value = "[]"
            self.assertFalse(dispatch(s, None)["message"])
            self.assertEqual(
                s.db.execute("SELECT state FROM jobs").fetchone()[0], "baseline"
            )
            with s.db:
                s.db.execute(
                    "UPDATE jobs SET state='launching',detail=?", (json.dumps(item),)
                )
            self.assertFalse(dispatch(s, None)["message"])
            self.assertEqual(
                s.db.execute("SELECT state FROM jobs").fetchone()[0], "uncertain"
            )
            cmd.return_value = json.dumps([{"id": "thr_fixture", "title": title(item)}])
            self.assertFalse(dispatch(s, None)["message"])
            self.assertEqual(
                tuple(
                    s.db.execute(
                        "SELECT state,thread_id FROM jobs WHERE id=?", (job_id(item),)
                    ).fetchone()
                ),
                ("dispatched", "thr_fixture"),
            )
            self.assertTrue(
                all(c.args[0][1:3] == ["thread", "list"] for c in cmd.call_args_list)
            )

    def test_new_error_launches_once_and_return_does_not_resolve_it(self):
        item = {
            "kind": "betterstack_triage",
            "key": "1/123",
            "revision": "0",
            "source": "betterstack",
            "external": "123",
        }
        s = self.store
        s.ingest(
            "betterstack",
            [{"external_id": "123", "title": "Synthetic error", "text": "Seen"}],
        )
        launched = []

        def cli(args):
            if args[1:3] == ["thread", "list"]:
                return json.dumps(launched)
            self.assertEqual(args[1:3], ["thread", "spawn"])
            self.assertIn("worktree", args)
            self.assertIn("visible", args)
            launched.append({"id": "thr_new", "title": title(item), "status": "active"})
            return json.dumps(launched[-1])

        with (
            patch("dispatch.Settings") as settings,
            patch("dispatch.betterstack", return_value={"application_id": 1}),
            patch("dispatch.selected_sources", return_value=["betterstack"]),
            patch("dispatch.candidates") as scan,
            patch("dispatch.command", side_effect=cli),
        ):
            settings.return_value.read.side_effect = lambda key: {
                "bb_context": {"project": {"id": "proj_x"}},
                "morning": {},
            }[key]
            scan.return_value = []
            self.assertFalse(dispatch(s, None)["message"])
            scan.return_value = [item]
            self.assertEqual(dispatch(s, None)["message"], "thr_new")
            self.assertFalse(dispatch(s, None)["message"])
            launched[0]["status"] = "idle"
            self.assertFalse(dispatch(s, None)["message"])
            self.assertEqual(len(launched), 1)
            self.assertEqual(
                s.db.execute("SELECT state FROM jobs").fetchone()[0], "returned"
            )
            self.assertIn(
                digest("betterstack", "123"), s._work_ids(digest("bb", "thr_new"))
            )
