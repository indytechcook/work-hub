import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dispatch import dispatch, error_name, job_id, title
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
        self.assertEqual(
            (arrival["at"], arrival["occurred_at"], arrival["rank"]), (12000, 4, 0)
        )

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
            patch("dispatch.chief_of_staff", return_value=False),
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
            patch("dispatch.chief_of_staff", return_value=False),
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


class ErrorTitleTests(unittest.TestCase):
    def test_error_threads_are_named_after_the_error(self):
        item = {"kind": "betterstack_triage", "key": "1/ab", "revision": "0", "external": "ab" * 32}
        named = dict(item, name=error_name(": Anthropic circuit breaker tripped"))
        self.assertTrue(title(named).startswith("Investigate prod error: Anthropic circuit breaker tripped · "))
        self.assertEqual(error_name("Net::ReadTimeout: Net::ReadTimeout with (Net..."), "Net::ReadTimeout: Net::ReadTimeout with (Net…")
        self.assertIn("Better Stack abab", title(dict(item, name="")))

    def test_jobs_recorded_without_a_name_keep_their_spawned_title(self):
        item = {"kind": "betterstack_triage", "key": "1/ab", "revision": "0", "external": "ab"}
        self.assertTrue(title(item).startswith("Error triage ab · "))


class ChiefOfStaffHandoffTests(unittest.TestCase):
    """With the Chief of Staff running, Work Hub detects and links; the plugin launches."""

    def setUp(self):
        isolate_storage(self)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = Store(Path(folder.name) / "fixture.db")
        self.addCleanup(self.store.close)
        self.item = {
            "kind": "betterstack_triage",
            "key": "1/77",
            "revision": "0",
            "source": "betterstack",
            "external": "77",
        }
        self.store.ingest(
            "betterstack", [{"external_id": "77", "title": "Synthetic", "text": "Seen"}]
        )
        self.queue = {}
        self.requests = []

    def cos(self, store, method, payload):
        if method == "enqueue":
            self.requests.append(payload)
            item = self.queue.setdefault(
                payload["key"],
                {"id": "c1", "sourceKey": payload["key"], "status": "queued", "threadId": None},
            )
            return {"item": item, "created": True}
        self.assertEqual(method, "itemStatus")
        return {"items": [self.queue[k] for k in payload["keys"] if k in self.queue]}

    def run_dispatch(self, scan):
        def cli(args, cwd=None, env=None):
            self.assertEqual(args[1:3], ["thread", "list"], "Work Hub must not spawn")
            return "[]"

        with (
            patch("dispatch.Settings") as settings,
            patch("dispatch.betterstack", return_value={"application_id": 1}),
            patch("dispatch.selected_sources", return_value=["betterstack"]),
            patch("dispatch.candidates", return_value=scan),
            patch("dispatch.chief_of_staff", return_value=True),
            patch("dispatch.cos_call", side_effect=self.cos),
            patch("dispatch.command", side_effect=cli),
        ):
            settings.return_value.read.side_effect = lambda key: {
                "bb_context": {"project": {"id": "proj_x"}},
                "morning": {},
            }[key]
            return dispatch(self.store, None)

    def state(self):
        return tuple(
            self.store.db.execute(
                "SELECT state,thread_id FROM jobs WHERE id=?", (job_id(self.item),)
            ).fetchone()
        )

    def test_hands_off_once_then_links_the_thread(self):
        self.run_dispatch([])  # establishes the baseline
        self.assertTrue(self.run_dispatch([self.item])["message"])
        self.assertEqual(self.state(), ("delegated", None))
        request = self.requests[0]
        self.assertEqual(request["key"], "work-hub:" + job_id(self.item))
        self.assertEqual(
            (request["mode"], request["markWorkDone"], request["environment"]),
            ("report", False, {"type": "worktree"}),
        )
        self.assertIn("Do not edit code", request["prompt"])
        # Queued behind the concurrency limit: nothing is launched twice.
        self.assertFalse(self.run_dispatch([self.item])["message"])
        self.assertEqual(len(self.requests), 1)
        self.queue[request["key"]]["threadId"] = "thr_cos"
        self.run_dispatch([self.item])
        self.assertEqual(self.state(), ("dispatched", "thr_cos"))
        self.assertIn(
            digest("betterstack", "77"), self.store._work_ids(digest("bb", "thr_cos"))
        )

    def test_lost_handoff_is_retried_with_the_same_key(self):
        self.run_dispatch([])
        with self.store.db:
            self.store.db.execute(
                "UPDATE jobs SET state='delegated',detail=?", (json.dumps(self.item),)
            )
        self.run_dispatch([self.item])  # unknown to the plugin: back to pending, then enqueued
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.state(), ("delegated", None))

    def test_cancelled_item_fails_the_job(self):
        self.run_dispatch([])
        self.run_dispatch([self.item])
        self.queue["work-hub:" + job_id(self.item)]["status"] = "cancelled"
        self.run_dispatch([self.item])
        self.assertEqual(self.state()[0], "failed")

    def test_review_requests_are_never_parked_as_baseline(self):
        review = {
            "kind": "private_review",
            "key": "https://github.com/o/r/pull/9",
            "revision": "a" * 40,
            "source": "github",
            "external": "PR_9",
            "number": 9,
            "repo": "o/r",
            "base": "b" * 40,
            "base_ref": "main",
            "head_ref": "feature",
        }
        # Recorded as baseline by an earlier version: it is promoted and reviewed.
        with self.store.db:
            self.store.db.execute(
                "INSERT INTO jobs(id,kind,source_key,revision,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (job_id(review), "private_review", review["key"], review["revision"], "baseline", 0, 0),
            )

        def cli(args, cwd=None, env=None):
            if args[:2] == ["gh", "api"]:
                return "me"
            self.assertEqual(args[1:3], ["thread", "list"], "Work Hub must not spawn")
            return "[]"

        with (
            patch("dispatch.Settings") as settings,
            patch("dispatch.selected_sources", return_value=["github"]),
            patch("dispatch.candidates", return_value=[review]),
            patch("dispatch.chief_of_staff", return_value=True),
            patch("dispatch.cos_call", side_effect=self.cos),
            patch("dispatch.command", side_effect=cli),
            patch("dispatch.review_checkout", return_value=Path("/tmp/review")),
            patch("morning_checks.review_candidate", return_value=((1, 1), "requested")),
        ):
            settings.return_value.read.side_effect = lambda key: {
                "bb_context": {"project": {"id": "proj_x"}},
                "morning": {"repository": "o/r"},
            }[key]
            dispatch(self.store, None)
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(self.requests[0]["handBack"])
        self.assertEqual(
            self.store.db.execute("SELECT state FROM jobs WHERE id=?", (job_id(review),)).fetchone()[0],
            "delegated",
        )
