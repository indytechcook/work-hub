import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from morning import brief, morning_targets, original_detail, source_check, verify_links
from morning_checks import projects, standup, review_candidate
from store import Store, digest
from test_support import isolate_storage


class MorningTests(unittest.TestCase):
    def test_review_candidate_requires_a_pending_direct_request_on_unreviewed_head(self):
        url = 'https://github.com/example/project/pull/7'
        pr = dict(state='OPEN',isDraft=False,headRefOid='a'*40,additions=12,deletions=3,
                  reviewRequests={'nodes':[{'requestedReviewer':{'login':'Sam'}}]},
                  commits={'nodes':[]}, reviews={'nodes':[]})
        def check(**updates):
            result = {'data':{'repository':{'pullRequest':{**pr,**updates}}}}
            with patch('morning_checks.command',return_value=json.dumps(result)):
                return review_candidate(url,'sam')
        self.assertEqual(check()[0],(1,15))
        self.assertEqual(check(reviews={'nodes':[{'state':'CHANGES_REQUESTED','commit':{'oid':'old'}}]})[0],(0,15))
        self.assertIsNone(check(reviews={'nodes':[{'state':'APPROVED','commit':{'oid':pr['headRefOid']}}]}))
        self.assertIsNone(check(commits={'nodes':[{'commit':{'statusCheckRollup':{'state':'FAILURE'}}}]}))
        self.assertIsNone(check(isDraft=True))
        self.assertIsNone(check(state='MERGED'))
        self.assertIsNone(check(reviewRequests={'nodes':[]}))

    def setUp(self):
        isolate_storage(self)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = Store(Path(folder.name) / "work.db")
        self.addCleanup(self.store.close)
        self.now = datetime.now(timezone.utc)

    def seed(self, src, key, due):
        body = "I will finish the sample."
        self.store.ingest(
            src,
            [
                dict(
                    title=key,
                    external_id=key,
                    text=body,
                    url="https://example.test/" + key,
                )
            ],
        )
        claim = self.store.claim_analysis()
        self.store.apply_analysis(
            claim["id"],
            claim["lease_token"],
            dict(
                summary="",
                next_action="Review sample",
                obligations=[
                    dict(
                        title=key,
                        owner="Sample user",
                        direction="i_owe",
                        due_at=due,
                        state="open",
                        quote=body,
                        confidence=0.99,
                    )
                ],
            ),
        )
        return digest(src, key)

    def test_overdue_rank_counts_retired_and_shared_membership(self):
        due = (self.now - timedelta(days=3)).timestamp()
        expired = self.seed("bb", "thr_oldest", due)
        self.seed("linear", "OLD-1", due - 86400)
        self.store.configure_tracking(["OLD"])
        for i in range(3):
            self.seed("bb", "thr_due" + str(i), due + 600 + i)
        self.store.save_intent(expired, 0, "Approve the sample")
        self.store.associate(expired, digest("bb", "thr_due0"), "add")
        self.store.save_day_plan(self.store.day_plan()["date"], 0, [], "My plan")
        with patch("morning.selected_sources", return_value=["linear"]):
            groups = {s["label"]: s for s in brief(self.store, True, self.now)}
        self.assertEqual(groups["Overdue"]["total"], 4)
        self.assertEqual(len(groups["Overdue"]["items"]), 3)
        self.assertEqual(
            groups["Suggested actions"]["items"][0]["title"], "Approve the sample"
        )
        self.assertEqual(
            groups["Suggested actions"]["items"][0]["url"],
            "https://example.test/thr_oldest",
        )
        self.assertEqual(self.store.day_plan()["work_ids"], [])

    def test_calendar_uses_local_start_rsvp_and_cancellation(self):
        day = "2026-06-12"

        def calendar(key, **extra):
            event = {
                "start": {"dateTime": day + "T09:30:00+00:00"},
                "attendees": [
                    {"email": "me@example.test", "responseStatus": "needsAction"}
                ],
                **extra,
            }
            self.store.ingest(
                "calendar", [dict(title=key, external_id=key, text=json.dumps(event))]
            )

        calendar("Active")
        calendar("Removed", status="cancelled")
        calendar("Old", start={"date": "2026-06-01"}, end={"date": day})
        with (
            patch("morning.selected_sources", return_value=["calendar"]),
            patch(
                "morning.load", return_value={"identity": {"email": "me@example.test"}}
            ),
        ):
            groups = {
                s["label"]: s
                for s in brief(
                    self.store, False, datetime.fromisoformat(day + "T10:00:00+00:00")
                )
            }
        self.assertEqual(groups["Calendar and week ahead"]["total"], 1)
        self.assertIn(
            "needsAction", groups["Calendar and week ahead"]["items"][0]["detail"]
        )

    def test_disabled_and_current_sources_do_not_launch_collectors(self):
        with patch(
            "collect.Collector.collect",
            side_effect=AssertionError("Unexpected live read"),
        ):
            self.assertEqual(source_check(self.store, "gmail")["state"], "skipped")
            self.store.ingest("gmail", [], checkpoint={"through": 42})
            with patch("morning.selected_sources", return_value=["gmail"]):
                self.assertEqual(source_check(self.store, "gmail")["state"], "checked")

    def test_verified_feedback_uses_latest_reply_and_actual_ci_states(self):
        thread = "From: Sender\nTime: first\nMessage TS: 1234567890.123456\nPlease review.\n=== THREAD REPLIES (1 total) ===\n--- Reply 1 ---\nFrom: Reviewer\nTime: later\nMessage TS: 1234567891.123456\nReview is posted."
        self.assertIn(
            "Latest captured reply · Reviewer: Review is posted.",
            original_detail("slack", json.dumps({"messages": thread})),
        )
        self.assertIn(
            "1 FAILURE",
            original_detail(
                "github",
                json.dumps(
                    {"state": "OPEN", "statusCheckRollup": [{"conclusion": "FAILURE"}]}
                ),
            ),
        )

    def test_partial_targeted_read_keeps_global_checkpoint(self):
        self.store.ingest("slack", [], checkpoint=42)
        wid = self.seed("bb", "thr_focus", (self.now + timedelta(hours=1)).timestamp())
        with self.store.db:
            self.store.db.execute(
                "UPDATE conversations SET url=? WHERE id=?",
                ("https://github.com/example/sample/pull/7", wid),
            )
        with (
            patch("morning.selected_sources", return_value=["github"]),
            patch(
                "source_reads.refresh_reference",
                side_effect=RuntimeError("secret sample"),
            ),
        ):
            result = verify_links(self.store, morning_targets(self.store, True))
            self.assertEqual(result["failed"], [0])
            self.assertNotIn("secret sample", str(result))
            self.assertEqual(morning_targets(self.store, False), [])

    def test_project_changes_paused_and_monday_milestones(self):
        row = dict(
            id="p1",
            name="Example",
            url="https://example.test/project",
            status={"type": "started", "name": "Active"},
            content="## Agent Board\n- **Deadline:** 2026-06-15",
            targetDate="2026-07-01",
            projectMilestones={
                "nodes": [{"name": "Later gate", "targetDate": "2026-08-12"}],
                "pageInfo": {"hasNextPage": False},
            },
        )
        page = {"nodes": [row], "pageInfo": {"hasNextPage": False}}
        with patch("morning_checks.project_page", return_value=page):
            result = projects(
                self.store, {"project_team_id": "example"}, date(2026, 6, 1)
            )[0]
            self.assertIn("14 days", result["items"][0]["detail"])
            self.assertIn("Later gate", result["items"][0]["detail"])
            row["status"] = {"type": "paused", "name": "Paused"}
            result = projects(
                self.store, {"project_team_id": "example"}, date(2026, 6, 2)
            )[0]
            self.assertIn("Changed", result["items"][0]["detail"])
            self.assertNotIn("Later gate", result["items"][0]["detail"])

    def test_weekend_and_completed_standup_skip_drafting(self):
        profile = {"dailybot_checkin": "example"}
        with patch("morning_checks.command") as cmd:
            self.assertEqual(
                standup(self.store, profile, date(2026, 6, 6), False)["state"],
                "skipped",
            )
            cmd.assert_not_called()
            cmd.return_value = json.dumps(
                {
                    "checkins": [
                        {
                            "id": "example",
                            "summary": {"current_user_has_response": True},
                        }
                    ]
                }
            )
            self.assertEqual(
                standup(self.store, profile, date(2026, 6, 8), False)["detail"],
                "Already completed.",
            )
            self.assertEqual(cmd.call_count, 1)
