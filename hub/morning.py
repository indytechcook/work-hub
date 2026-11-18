"""Build the morning brief from durable evidence. Suggestions never save a plan."""

from collections import defaultdict
from datetime import date, datetime, timedelta
import json
import time

from settings import load, selected_sources, timezone
from source_preview import slack_messages, slack_preview
from references import reference


def item(title, detail="", url="", work_id=""):
    return dict(
        title=str(title)[:350],
        detail=str(detail)[:800],
        url=url or "",
        work_id=work_id or "",
    )


def section(label, rows=(), detail="", state="checked", limit=12):
    rows = list(rows)
    return dict(
        label=label, state=state, detail=detail, total=len(rows), items=rows[:limit]
    )


def parsed(raw):
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def source_check(store, name, bb_enabled=False):
    from collect import Collector

    enabled = bb_enabled if name == "bb" else name in selected_sources()
    if not enabled:
        return section(name, detail="Collection is off.", state="skipped")

    def health():
        return store.db.execute(
            "SELECT * FROM sources WHERE name=?", (name,)
        ).fetchone()

    row = health()
    fresh = (
        row["state"] == "current" and (row["last_success"] or 0) >= time.time() - 600
    )
    if not fresh and name != "bb":
        if name == "linear":
            from morning_checks import bind_linear

            bind_linear()
        result = Collector(store).collect(name)
        if result["state"] == "already_running":
            return section(
                name,
                detail="A collector already holds this source. Showing saved receipts.",
                state="partial",
            )
        row = health()
    stamp = (
        datetime.fromtimestamp(row["last_success"], timezone()).strftime(
            "%b %d %H:%M %Z"
        )
        if row["last_success"]
        else "never"
    )
    state = {"current": "checked", "error": "failed"}.get(row["state"], "partial")
    detail = f"Last successful scan {stamp}. {row['error'] or row['scope']}"
    return section(name, detail=detail, state=state)


def brief(store, bb_enabled, now=None):
    tz = timezone()
    now = now or datetime.now(tz)
    today = now.date()
    end = today + timedelta(days=7)
    previous_workday = today - timedelta(days=1)
    while previous_workday.weekday() >= 5:
        previous_workday -= timedelta(days=1)
    start = datetime.combine(previous_workday, datetime.min.time(), tz).timestamp()
    enabled = set(selected_sources())
    if bb_enabled:
        enabled.add("bb")
    previous = store.previous_day_plan() or {}
    plan = store.day_plan(today.isoformat())
    groups = defaultdict(list)

    def days_back(n):
        day = today
        for _ in range(n):
            day -= timedelta(days=1)
            while day.weekday() >= 5:
                day -= timedelta(days=1)
        return day

    chase_date = days_back(2)
    escalate_date = days_back(10)
    candidates = []
    waiting_people = {}
    latest_replies = {}
    inactive = set()
    for record in store.db.execute(
        "SELECT value FROM meta WHERE key LIKE 'morning_projects:%'"
    ):
        inactive.update(
            p["name"]
            for p in parsed(record[0]).values()
            if p.get("status") in ("paused", "backlog", "completed", "canceled")
        )

    def candidate(rank, row):
        if projects[row["work_id"]] and set(projects[row["work_id"]]).issubset(
            inactive
        ):
            return
        if row["work_id"]:
            candidates.append((rank, row))

    def evidence(wid):
        rows = store.db.execute(
            """SELECT c.source,c.external_id,c.url FROM work_memberships m JOIN conversations c
            ON c.id=m.conversation_id WHERE m.work_id=? ORDER BY c.updated_at DESC""",
            (wid,),
        )
        return next(
            (
                r["url"]
                for r in rows
                if r["source"] in enabled
                and not store.is_tracking(r["source"], r["external_id"])
            ),
            "",
        )

    projects = defaultdict(list)
    for r in store.db.execute("SELECT * FROM work_project_names ORDER BY name"):
        projects[r["work_id"]].append(r["name"])

    def action(wid, title, reason, url):
        intent = store.intent(wid)
        area = " · ".join(projects[wid]) or intent["project"]
        return item(
            intent["next_action"] or title,
            " · ".join(filter(None, [area, reason])),
            url,
            wid,
        )

    rows = store.db.execute("""SELECT o.*,c.source,c.url,c.external_id,w.status,e.occurred_at AS ask_at FROM obligations o
        JOIN conversations c ON c.id=o.conversation_id JOIN work w ON w.id=o.work_id JOIN events e ON e.id=o.evidence_event
        ORDER BY o.due_at IS NULL,o.due_at,o.updated_at DESC,o.id""")
    for r in rows:
        if r["source"] not in enabled or store.is_legacy_obligation(r["id"]):
            continue
        if r["state"] == "fulfilled" and r["updated_at"] >= start:
            groups["Completed since previous workday"].append(
                item(
                    r["title"],
                    r["resolution"] or "Marked fulfilled in Work Hub",
                    r["url"],
                    r["work_id"],
                )
            )
        if r["state"] not in ("open", "needs_review"):
            continue
        due = (
            datetime.fromtimestamp(r["due_at"], tz).date()
            if r["due_at"] is not None
            else None
        )
        timing = "Due " + due.isoformat() if due else "No promised date"
        entry = item(
            r["title"],
            f"{r['owner']} · {timing} · {r['evidence_quote']}",
            r["url"],
            r["work_id"],
        )
        if due and due < today:
            groups["Overdue"].append(entry)
        elif due and due <= end:
            groups["Due in seven days"].append(entry)
        if r["direction"] == "they_owe":
            groups["Waiting for a reply"].append(entry)
            if r["source"] in ("slack", "gmail"):
                ask_date = datetime.fromtimestamp(r["ask_at"], tz).date()
                if ask_date <= chase_date or due and due <= today:
                    owner = r["owner"].strip().casefold()
                    if owner:
                        waiting_people[owner] = min(
                            waiting_people.get(owner, r["ask_at"]), r["ask_at"]
                        )
                    groups["Follow-ups to check"].append(
                        item(
                            r["title"],
                            f"{r['owner']} · Ask evidence {ask_date}. "
                            + (
                                "Review the blocker before another nudge."
                                if ask_date < escalate_date
                                else "Check for a newer reply or work already delivered."
                            ),
                            r["url"],
                            r["work_id"],
                        )
                    )
            if datetime.fromtimestamp(r["updated_at"], tz).date() <= today - timedelta(
                days=7
            ):
                groups["Stale commitments"].append(entry)
        if r["state"] == "needs_review":
            groups["Needs a decision"].append(entry)
        if r["direction"] == "i_owe":
            groups["Owed by you"].append(entry)
        if r["status"] == "active" and (
            r["direction"] == "i_owe" or r["state"] == "needs_review"
        ):
            rank = 8
            if due and due < today and r["direction"] == "i_owe":
                rank = 0
            elif due and due <= today:
                rank = 2
            elif r["state"] == "needs_review":
                rank = 3
            elif due and due <= end:
                rank = 5
            candidate(
                rank,
                action(r["work_id"], r["title"], timing + " · " + r["title"], r["url"]),
            )
    for r in store.db.execute(
        "SELECT id,title,next_action,status,focus FROM work WHERE status!='done' ORDER BY updated_at DESC,id"
    ):
        wid = r["id"]
        carry = wid in previous.get("work_ids", [])
        if not (carry or r["focus"] or wid in plan["work_ids"]):
            continue
        entry = action(wid, r["next_action"] or r["title"], r["title"], evidence(wid))
        if carry:
            groups["Carryover"].append(entry)
        candidate(1 if wid in plan["work_ids"] else 4 if carry else 6, entry)
    email = load().get("identity", {}).get("email", "").casefold()
    agenda = []
    threshold = previous.get("updated_at") or now.timestamp() - 86400
    for r in store.db.execute("""SELECT c.*,e.text,e.occurred_at,e.received_at,w.status work_status FROM conversations c JOIN events e ON e.id=c.latest_event
        JOIN work w ON w.id=c.work_id ORDER BY e.received_at DESC"""):
        src = r["source"]
        if src not in enabled or store.is_tracking(src, r["external_id"]):
            continue
        entry = item(r["title"], src, r["url"], r["work_id"])
        if src == "slack" and waiting_people:
            import re

            for message in re.split(
                r"^--- Reply [^\n]+\n", slack_messages(r["text"]), flags=re.M
            ):
                reply = slack_preview(message)
                if not reply:
                    continue
                person = reply["author"].strip().casefold()
                if (
                    person not in waiting_people
                    or reply["at"] <= waiting_people[person]
                ):
                    continue
                if (
                    person not in latest_replies
                    or latest_replies[person][0] < reply["at"]
                ):
                    latest_replies[person] = (
                        reply["at"],
                        item(reply["author"], reply["text"], r["url"], r["work_id"]),
                    )
        if src == "calendar":
            event = parsed(r["text"])
            begins = event.get("start") or {}
            ends = event.get("end") or {}
            if (
                event.get("status") == "cancelled"
                or not isinstance(begins, dict)
                or not isinstance(ends, dict)
            ):
                continue

            def instant(v):
                if v.get("dateTime"):
                    return datetime.fromisoformat(v["dateTime"]).astimezone(tz)
                return datetime.combine(
                    date.fromisoformat(v["date"]), datetime.min.time(), tz
                )

            try:
                first = instant(begins)
                last = instant(ends) if ends else first
            except (ValueError, KeyError, TypeError):
                continue
            if (
                last.date() < today
                or (
                    last.date() == today
                    and last.time() == datetime.min.time()
                    and first < last
                )
                or first.date() > end
            ):
                continue
            rsvp = next(
                (
                    a.get("responseStatus", "")
                    for a in event.get("attendees", [])
                    if isinstance(a, dict)
                    and email
                    and a.get("email", "").casefold() == email
                ),
                "",
            )
            detail = first.strftime(
                "%a %b %d · %H:%M %Z"
                if begins.get("dateTime")
                else "%a %b %d · All day"
            )
            agenda.append(
                (
                    first,
                    item(
                        r["title"],
                        detail + (" · " + rsvp if rsvp else ""),
                        r["url"],
                        r["work_id"],
                    ),
                )
            )
        if src == "bb":
            status = parsed(r["text"]).get("status")
            h = store.handoff(r["id"]) or {}
            if status in ("active", "starting"):
                groups["Running conversations"].append(entry)
            elif (
                h.get("status")
                in ("blocked", "needs_input", "ready_for_review", "failed")
                and r["work_status"] != "done"
            ):
                entry = item(
                    r["title"],
                    h.get("blocker") or h.get("next_action") or h["status"],
                    r["url"],
                    r["work_id"],
                )
                groups["Needs a decision"].append(entry)
                candidate(3, entry)
        if r["received_at"] >= threshold and src in (
            "slack",
            "gmail",
            "meetings",
            "drive",
            "betterstack",
            "linear",
        ):
            preview = slack_preview(r["text"]) if src == "slack" else None
            groups["Recent arrivals"].append(
                item(
                    preview["text"] if preview else r["title"],
                    src,
                    r["url"],
                    r["work_id"],
                )
            )
    groups["Newer messages from people you are waiting on"] = [
        entry for _, entry in sorted(latest_replies.values(), key=lambda pair: -pair[0])
    ]
    groups["Calendar and week ahead"] = [
        entry for _, entry in sorted(agenda, key=lambda v: v[0])
    ]
    picked = set()
    for _, entry in sorted(candidates, key=lambda v: v[0]):
        if entry["work_id"] not in picked:
            groups["Suggested actions"].append(entry)
            picked.add(entry["work_id"])
    pending = store.db.execute(
        "SELECT COUNT(*) FROM events WHERE analysis_state IN ('pending','running')"
    ).fetchone()[0]
    details = {
        "Suggested actions": "Overdue personal commitments first. Choose suggestions to add to your plan.",
        "Carryover": (
            "Previous plan " + previous["date"] + ". " + previous.get("note", "")
            if previous
            else "No previous saved plan."
        ),
        "Recent arrivals": f"Latest receipts since the previous plan, or the past 24 hours on first use. {pending} events await analysis.",
        "Waiting for a reply": "Open commitments owed by others. Check the original conversation before drafting a nudge.",
        "Follow-ups to check": "Review dates use the recorded ask and two business days. A promised date can bring the review forward.",
        "Newer messages from people you are waiting on": "Latest captured Slack message for each exact author match after an open ask. Check its relevance and the source thread before drafting a nudge.",
        "Stale commitments": "Waiting obligations last updated at least seven days ago.",
        "Overdue": "Open commitments past their local due date, oldest first.",
    }
    sections = [
        section(
            label,
            groups[label],
            details.get(label, ""),
            limit=3 if label == "Overdue" else 10,
        )
        for label in (
            "Suggested actions",
            "Overdue",
            "Needs a decision",
            "Calendar and week ahead",
            "Due in seven days",
            "Owed by you",
            "Waiting for a reply",
            "Follow-ups to check",
            "Newer messages from people you are waiting on",
            "Stale commitments",
            "Running conversations",
            "Carryover",
            "Completed since previous workday",
            "Recent arrivals",
        )
    ]
    closeout = store._record("closeout:last", None)
    if closeout:
        gaps = [s["source"] for s in closeout["coverage"] if s["state"] != "checked"]
        sections.append(section("Last deep close-out",
            [item(w["title"], work_id=w["id"]) for w in closeout["carryover"]],
            f"{closeout['date']}: {closeout['folded']} duplicate cards consolidated, "
            f"{closeout['duplicate_groups']} possible duplicate groups, {closeout['related_total']} related pairs for review. "
            f"Coverage gaps: {', '.join(gaps) or 'none reported'}."))
    return sections


def morning_targets(store, bb_enabled):
    enabled = set(selected_sources())
    allowed = enabled | ({"bb"} if bb_enabled else set())
    rows = store.db.execute("""SELECT DISTINCT c.id,c.source,c.external_id,c.url,c.title,r.url linked
        FROM conversations c JOIN work_memberships m ON m.conversation_id=c.id JOIN work w ON w.id=m.work_id LEFT JOIN source_references r ON r.conversation_id=c.id
        WHERE (w.focus=1 OR w.status='waiting' OR EXISTS(SELECT 1 FROM obligations o WHERE o.conversation_id=c.id AND o.state IN ('open','needs_review')))
        ORDER BY w.focus DESC,c.updated_at DESC""")
    targets = {}

    def add_target(url):
        try:
            ref = reference(url or "")
        except ValueError:
            return
        if (
            ref
            and ref["source"] in enabled
            and ref["source"] in ("github", "linear", "slack")
            and not store.is_tracking(ref["source"], ref.get("id", ""))
        ):
            targets.setdefault(ref["key"], ref)

    for group in brief(store, bb_enabled):
        if group["label"] in ("Suggested actions", "Follow-ups to check"):
            for entry in group["items"]:
                add_target(entry["url"])
    for row in rows:
        if row["source"] not in allowed or store.is_tracking(
            row["source"], row["external_id"]
        ):
            continue
        for url in (row["url"], row["linked"]):
            add_target(url)
    return [ref["url"] for ref in targets.values()]


def verify_links(store, urls, retry_errors=False):
    from source_reads import refresh_reference
    from morning_checks import bind_linear

    if len(urls) > 8:
        raise ValueError("Too many references in one batch")
    project = None
    items = []
    failed = []
    for index, url in enumerate(urls):
        ref = reference(url)
        if (
            not ref
            or ref["source"] not in ("slack", "linear", "github")
            or store.is_tracking(ref["source"], ref.get("id", ""))
        ):
            raise ValueError("Unsupported morning reference")
        try:
            if ref["source"] == "linear" and not project:
                project = bind_linear()
            cid = refresh_reference(
                store, ref["url"], project, retry_error=retry_errors
            )
            row = store.db.execute(
                "SELECT c.title,e.text FROM conversations c JOIN events e ON e.id=c.latest_event WHERE c.id=?",
                (cid,),
            ).fetchone()
            detail = original_detail(ref["source"], row["text"])
            items.append(item(row["title"], detail, ref["url"]))
        except Exception:
            failed.append(index)
            items.append(item(ref["url"], "Could not verify this source.", ref["url"]))
    return {"items": items, "failed": failed}


def original_detail(source, raw):
    body = parsed(raw)
    if source == "slack":
        import re

        messages = slack_messages(raw)
        parts = re.split(r"^--- Reply [^\n]+\n", messages, flags=re.M)
        latest = slack_preview(parts[-1])
        if latest:
            kind = "Latest captured reply" if len(parts) > 1 else "Parent"
            return f"{kind} · {latest['author']}: {latest['text']}"
        return "Original read; latest reply could not be summarized."
    if source == "github":
        details = [str(body.get("state") or "Original read")]
        if body.get("reviewDecision"):
            details.append(body["reviewDecision"])
        checks = body.get("statusCheckRollup")
        if isinstance(checks, list):
            states = defaultdict(int)
            for check in checks:
                state = (
                    check.get("conclusion")
                    or check.get("state")
                    or check.get("status")
                    or "unknown"
                )
                states[state] += 1
            details.append(
                "CI: "
                + (
                    ", ".join(f"{n} {state}" for state, n in sorted(states.items()))
                    or "no checks reported"
                )
            )
        return " · ".join(details)
    return next(
        (l for l in raw.splitlines() if l.startswith("State:")), "Original read"
    )


def morning_context(store):
    from storage import Store as ConfigStore

    plan = store.day_plan()
    project = (ConfigStore().read("bb_context") or {}).get("project", {}).get("id")
    work = []
    for wid in plan["work_ids"]:
        row = store.db.execute("SELECT title FROM work WHERE id=?", (wid,)).fetchone()
        if row:
            work.append(dict(id=wid, title=row[0]))
    from hashlib import sha256

    scope = [load(), ConfigStore().read("morning"), project]
    return dict(
        scope=sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest(),
        date=plan["date"],
        project_id=project,
        thread_id=store._record("morning_thread:" + plan["date"], None),
        version=plan["version"],
        note=plan["note"],
        work=work,
    )


def record_morning_thread(store, date, thread_id):
    import re

    if store.day_plan()["date"] != date or not re.fullmatch(
        "thr_[a-zA-Z0-9_-]{1,90}", thread_id
    ):
        raise ValueError("Invalid planning thread or date")
    with store.db:
        store._put_record("morning_thread:" + date, thread_id)
    return {"ok": True}


def last_morning_result(store):
    return store._record("morning_last_result", None)


def save_morning_result(store, run):
    if run.get("finished") is not True or not isinstance(
        run.get("started"), (int, float)
    ):
        raise ValueError("Expected a finished morning run")
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        previous = last_morning_result(store)
        if not previous or previous["started"] <= run["started"]:
            store._put_record("morning_last_result", run)
    return {"ok": True}
