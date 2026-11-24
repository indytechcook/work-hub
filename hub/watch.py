"""Scheduled local observations. Source reads use the same per-user opt-ins as collection."""

import fcntl
import json
import re
import subprocess
import time
from datetime import datetime
from email.utils import parseaddr

from collect import Collector
from presentation import fingerprint
from references import reference
from settings import identity, selected_sources, timezone
from source_preview import slack_messages
from source_reads import check_source_scope, read_reference


def health(store, bb_enabled=False, now=None):
    now = time.time() if now is None else now
    enabled = set(selected_sources())
    if bb_enabled:
        enabled.add("bb")
    zone = timezone()
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        previous = store._record("source_health", {})
        current = {}
        symptoms = {}
        for row in store.db.execute("SELECT * FROM sources"):
            name, state, last = row["name"], row["state"], row["last_success"]
            if name not in enabled:
                continue
            if state == "current" and (not last or now - last > 900):
                state = "stale"
            if state != "current":
                stamp = (
                    datetime.fromtimestamp(last, zone).strftime("%b %d %H:%M %Z")
                    if last
                    else "never"
                )
                symptoms[name] = (
                    f"{state}. Last successful scan: {stamp}. Recent activity may be missing."
                )
        oldest_pending = store.db.execute(
            "SELECT MIN(received_at) FROM events WHERE analysis_state='pending'"
        ).fetchone()[0]
        last_analysis = store.db.execute(
            "SELECT MAX(json_extract(value,'$.analyzed_at')) FROM meta WHERE key GLOB 'checkpoint:*'"
        ).fetchone()[0]
        if (
            oldest_pending is not None
            and now - max(last_analysis or 0, oldest_pending) > 3600
        ):
            symptoms["analysis"] = (
                "Queued receipts have had no analysis result for an hour. Check the daily allowance and worker."
            )
        changed = []
        for name, detail in symptoms.items():
            old = previous.get(name, {})
            since = old.get("since", now)
            active = now - since >= 14 * 60
            current[name] = {"since": since, "active": active, "detail": detail}
            if active and not old.get("active"):
                store.change("coverage_alert", name, f"Check {name} coverage")
                changed.append(name)
        for name in previous.keys() - current.keys():
            if previous[name]["active"]:
                store.change("coverage_cleared", name, f"{name} coverage alert cleared")
                changed.append(name)
        store._put_record("source_health", current)
    return changed


def messages(source, body):
    if source == "slack":
        return [
            (ts, float(ts), author.casefold())
            for author, ts in re.findall(
                r"^From: ([^\n]+)\nTime: [^\n]+\nMessage TS: (\d{10}\.\d{6})\s*$",
                slack_messages(body),
                re.MULTILINE,
            )
        ]
    records = json.loads(body)["messages"]
    return [
        (
            m["id"],
            int(m["internal_date"]) / 1000,
            parseaddr(
                next(
                    (h["value"] for h in m["headers"] if h["name"].lower() == "from"),
                    "",
                )
            )[1].casefold(),
        )
        for m in records
    ]


def watch_target(url):
    ref = reference(url)
    if ref and ref["source"] == "slack":
        check_source_scope(ref)
        return "slack", ref["url"]
    if "gmail" in selected_sources() and re.fullmatch(
        r"https://mail\.google\.com/mail/u/\d+/#(?:all|inbox|sent)/[0-9a-fA-F]+", url
    ):
        return "gmail", "https://mail.google.com/mail/u/0/#all/" + url.rsplit("/", 1)[1]
    raise ValueError("Use a configured Slack permalink or Gmail conversation link")


def register_watch(store, url, sent_id, work_id=None):
    source, url = watch_target(url)
    pattern = r"\d{10}\.\d{6}" if source == "slack" else r"[a-fA-F0-9]{1,32}"
    if not isinstance(sent_id, str) or not re.fullmatch(pattern, sent_id):
        raise ValueError("Supply the sent message timestamp or Gmail message ID")
    key = "reply_watch:" + fingerprint(url, sent_id)
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        if (
            work_id
            and not store.db.execute(
                "SELECT 1 FROM work WHERE id=?", (work_id,)
            ).fetchone()
        ):
            raise ValueError("Unknown work item")
        if not store._record(key, None):
            store._put_record(
                key,
                {
                    "source": source,
                    "url": url,
                    "sent": sent_id,
                    "work": work_id,
                    "checked": 0,
                },
            )
    return {"watching": True}


def read_target(store, source, url):
    source, url = watch_target(url)
    if source == "slack":
        item = read_reference(reference(url))
        stamps = messages(source, item["text"])
        if not stamps:
            raise ValueError("Missing Slack messages")
        item["occurred_at"] = max(m[1] for m in stamps)
        item["text"] = slack_messages(item["text"])
    else:
        collector = Collector(store)
        item = collector.gmail_thread(collector.google(), url.rsplit("/", 1)[1])
        if not item:
            raise ValueError("Empty Gmail thread")
    from store import digest

    cid = digest(source, item["external_id"])
    prior = store.db.execute(
        "SELECT title FROM conversations WHERE id=?", (cid,)
    ).fetchone()
    if prior and source == "slack":
        item["title"] = prior[0]
    item["version"] = digest(item["title"], item["text"])
    store.ingest(source, [item], targeted=True)
    return cid, messages(source, item["text"])


def replies(store, limit=4):
    with (store.path.parent / "reply-check.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"checked": 0, "arrivals": 0, "errors": 0}
        return check_replies(store, limit)


def from_other(author, sent, owner):
    if not author:
        return False
    if sent:
        return author != sent[2]
    return (
        author != owner["email"].casefold()
        and re.sub(r" \([^()]+\)$", "", author) != owner["name"].casefold()
    )


def check_replies(store, limit):
    now = time.time()
    enabled = set(selected_sources())
    watches = {
        row[0]: json.loads(row[1])
        for row in store.db.execute(
            "SELECT key,value FROM meta WHERE key LIKE 'reply_watch:%'"
        )
    }
    for (
        row
    ) in store.db.execute("""SELECT o.id,c.source,c.url,e.occurred_at FROM obligations o
        JOIN conversations c ON c.id=o.conversation_id JOIN events e ON e.id=o.evidence_event
        WHERE c.source IN ('gmail','slack') AND o.direction='they_owe' AND o.state='open'"""):
        key = "reply_watch:" + row["id"]
        watches.setdefault(
            key,
            {
                "source": row["source"],
                "url": row["url"],
                "after": row["occurred_at"],
                "checked": 0,
                "obligation": row["id"],
            },
        )
    candidates = []
    for key, w in watches.items():
        if w.get("reply") or w["source"] not in enabled or now - w["checked"] < 600:
            continue
        if (
            w.get("obligation")
            and not store.db.execute(
                "SELECT 1 FROM obligations WHERE id=? AND state='open'",
                (w["obligation"],),
            ).fetchone()
        ):
            continue
        candidates.append((key, w))
    if not candidates:
        return {"checked": 0, "arrivals": 0, "errors": 0}
    owner = identity()
    batch = sorted(candidates, key=lambda entry: (entry[1]["checked"], entry[0]))[
        :limit
    ]
    changed = []
    for key, w in batch:
        try:
            cid, records = read_target(store, w["source"], w["url"])
            sent = next((m for m in records if m[0] == w.get("sent")), None)
            after = sent[1] if sent else w.get("after")

            reply = next(
                (
                    m
                    for m in records
                    if after is not None
                    and m[1] > after
                    and from_other(m[2], sent, owner)
                ),
                None,
            )
            if w.get("work") and w["work"] not in store._work_ids(cid):
                store.associate(cid, w["work"], "add")
            w.pop("error", None)
            if w.get("sent") and sent is None:
                raise ValueError("Sent message outside coverage")
            if reply:
                w.update(reply=reply[0], at=reply[1], cid=cid)
                changed.append(key)
        except (
            RuntimeError,
            OSError,
            ValueError,
            KeyError,
            TypeError,
            subprocess.TimeoutExpired,
            ExceptionGroup,
        ) as error:
            w["error"] = type(error).__name__
        w["checked"] = now
        with store.db:
            store._put_record(key, w)
            if key in changed:
                store.change(
                    "reply_received", w["cid"], "New reply on a watched conversation"
                )
    return {
        "checked": len(batch),
        "arrivals": len(changed),
        "errors": sum(bool(w.get("error")) for _, w in batch),
    }
