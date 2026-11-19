"""Optional, on-demand reads. Only trusted private configuration selects integrations."""

import ast
import asyncio
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import re
import subprocess

import httpx
from morning import brief, item, section, source_check
from references import reference
from settings import selected_sources, timezone
from storage import Store as ConfigStore


def command(argv):
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        check=True,
        timeout=45,
        stdin=subprocess.DEVNULL,
    )
    if len(result.stdout) > 2_000_000:
        raise ValueError("Oversized morning response")
    return result.stdout


def bind_linear():
    ctx = ConfigStore().read("bb_context") or {}
    project = ctx.get("project", {}).get("id") or os.environ.get("BB_PROJECT_ID")
    if not project:
        raise ValueError("Configure a bound BB project for Linear reads")
    os.environ["BB_PROJECT_ID"] = project
    return project


def checkout_changes(path):
    changes = command(
        [
            "git",
            "--no-optional-locks",
            "-C",
            str(path),
            "status",
            "--porcelain=v2",
            "--branch",
        ]
    )
    ahead = re.search(r"^# branch.ab \+(\d+) -(\d+)$", changes, flags=re.M)
    dirty = any(line and not line.startswith("#") for line in changes.splitlines())
    details = ["local changes" if dirty else "clean"]
    if ahead:
        details.append(f"{ahead[1]} ahead, {ahead[2]} behind last fetched upstream")
    else:
        details.append("no tracked upstream")
    return ", ".join(details)


def checkout(profile):
    path = profile.get("checkout")
    if not path:
        return section(
            "Checkout and worktrees", state="skipped", detail="No checkout configured."
        )
    branch = (
        command(["git", "-C", path, "branch", "--show-current"]).strip() or "detached"
    )
    raw = command(["git", "-C", path, "worktree", "list", "--porcelain", "-z"])
    rows = []
    occupied = 0
    slots = profile.get("slots", {})
    for block in raw.strip("\0").split("\0\0"):
        fields = dict(field.partition(" ")[::2] for field in block.split("\0"))
        worktree = Path(fields["worktree"])
        if worktree == Path(path):
            continue
        detail = fields.get("branch", "Detached").removeprefix("refs/heads/")
        if slots and (worktree / ".env").is_file():
            with (worktree / ".env").open(errors="replace") as stream:
                for line in stream:
                    key, _, value = line.strip().partition("=")
                    if key == slots["port_key"] and value.isdigit():
                        offset = int(value) - slots["base"]
                        if offset >= slots["stride"] and offset % slots["stride"] == 0:
                            occupied += 1
                            detail += " · full slot"
                        break
        try:
            detail += " · " + checkout_changes(worktree)
        except (OSError, subprocess.SubprocessError):
            detail += " · working tree status unavailable"
        rows.append(item(worktree.name, detail))
    expected = profile.get("base_branch")
    summary = f"Main checkout on {branch} ({checkout_changes(path)})" + (
        f" (expected {expected})" if expected and branch != expected else ""
    )
    if slots:
        summary += f". {occupied}/{slots['capacity']} full slots occupied"
    return section(
        "Checkout and worktrees",
        rows,
        summary + ". Cleanup requires a separate worktree safety check.",
    )


def review_candidate(url, viewer):
    ref = reference(url)
    if not ref or ref.get("kind") != "pull":
        raise ValueError("Expected a GitHub PR")
    owner, repo = ref["repo"].split("/")
    query = """query($owner:String!,$repo:String!,$number:Int!,$viewer:String!){
      repository(owner:$owner,name:$repo){pullRequest(number:$number){
        state isDraft headRefOid additions deletions
        commits(last:1){nodes{commit{statusCheckRollup{state}}}}
        reviewRequests(first:100){nodes{requestedReviewer{... on User{login}}}}
        reviews(author:$viewer,last:1,states:[APPROVED,CHANGES_REQUESTED]){nodes{state commit{oid}}}
      }}}"""
    data = json.loads(command(["gh","api","graphql","-f","query="+query,"-f","owner="+owner,
                               "-f","repo="+repo,"-F",f"number={ref['number']}","-f","viewer="+viewer]))
    pr = data["data"]["repository"]["pullRequest"]
    requests = pr["reviewRequests"]["nodes"]
    if pr["state"] != "OPEN" or pr["isDraft"] or not any(
        (r.get("requestedReviewer") or {}).get("login","").casefold() == viewer.casefold() for r in requests
    ):
        return None
    reviews = pr["reviews"]["nodes"]
    if reviews and (reviews[0].get("commit") or {}).get("oid") == pr["headRefOid"]:
        return None
    checks = pr["commits"]["nodes"]
    if checks and (checks[0]["commit"].get("statusCheckRollup") or {}).get("state") in ("ERROR","FAILURE"):
        return None
    revisit = bool(reviews and reviews[0]["state"] == "CHANGES_REQUESTED")
    reason = "New commits since you requested changes" if revisit else "Your review is requested on this revision"
    size = pr["additions"] + pr["deletions"]
    return ((0 if revisit else 1,size), f"{reason} · +{pr['additions']}/−{pr['deletions']} · {pr['headRefOid'][:12]}")


def pull_requests(store, profile):
    repo = profile.get("repository")
    result = []
    for flag, label in [
        ("--author", "Your open PRs"),
        ("--review-requested", "PRs requesting your review"),
    ]:
        args = [
            "gh",
            "search",
            "prs",
            flag,
            "@me",
            "--state",
            "open",
            "--limit",
            "51",
            "--json",
            "number,url,title,isDraft,author",
        ]
        if repo:
            args.extend(["--repo", repo])
        rows = json.loads(command(args))
        entries = []
        failures = 0
        picks = []
        viewer = None
        if flag == "--review-requested" and rows:
            try:
                viewer = command(["gh","api","user","--jq",".login"]).strip()
            except (OSError, ValueError, subprocess.SubprocessError):
                failures += 1
        for row in rows[:25]:
            url = row["url"]
            details = ["Draft" if row["isDraft"] else "Open", row["author"]["login"]]
            if flag == "--author":
                try:
                    pr = json.loads(
                        command(
                            [
                                "gh",
                                "pr",
                                "view",
                                url,
                                "--json",
                                "reviewDecision,statusCheckRollup,latestReviews,comments",
                            ]
                        )
                    )
                    checks = pr.get("statusCheckRollup") or []
                    failures_ci = [
                        c.get("name") or c.get("context") or "check"
                        for c in checks
                        if (c.get("conclusion") or c.get("state"))
                        in (
                            "FAILURE",
                            "ERROR",
                            "CANCELLED",
                            "TIMED_OUT",
                            "ACTION_REQUIRED",
                        )
                    ]
                    if pr.get("reviewDecision"):
                        details.append(pr["reviewDecision"].replace("_", " ").lower())
                    details.append(
                        "Failing: " + ", ".join(failures_ci[:4])
                        if failures_ci
                        else f"{len(checks)} reported checks"
                    )
                    comments = [
                        c
                        for c in (pr.get("latestReviews") or [])
                        + (pr.get("comments") or [])
                        if c.get("body")
                    ]
                    ref = reference(url)
                    if ref and ref["source"] == "github" and ref["kind"] == "pull":
                        path = f"repos/{ref['repo']}/pulls/{ref['number']}/comments?sort=updated&direction=desc&per_page=10"
                        inline = json.loads(command(["gh", "api", path]))
                        comments.extend(
                            {"body": c["body"], "createdAt": c["updated_at"]}
                            for c in inline
                            if c.get("body")
                        )
                    if comments:
                        latest = max(
                            comments,
                            key=lambda c: (
                                c.get("createdAt") or c.get("submittedAt") or ""
                            ),
                        )
                        details.append("Latest feedback: " + latest["body"][:220])
                except Exception:
                    failures += 1
                    details.append("Review and CI details unavailable")
            wid = store.db.execute(
                "SELECT work_id FROM conversations WHERE source='github' AND url=?",
                (url,),
            ).fetchone()
            entries.append(
                item(
                    "#" + str(row["number"]) + " " + row["title"],
                    " · ".join(details),
                    url,
                    wid[0] if wid else "",
                )
            )
            if viewer and not row["isDraft"]:
                try:
                    pick = review_candidate(url,viewer)
                except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
                    failures += 1
                    continue
                if pick:
                    rank, reason = pick
                    picks.append((rank, {**entries[-1], "detail": reason}))
        result.append(
            section(
                label,
                entries,
                f"{len(rows)}{'+' if len(rows) == 51 else ''} found. {failures} detail reads failed. Showing up to 25.",
                "partial" if failures or len(rows) > 25 else "checked",
                limit=25,
            )
        )
        if flag == "--review-requested":
            ranked = [entry for _,entry in sorted(picks,key=lambda pair:pair[0])]
            result.append(section("Review candidates", ranked, "Direct review requests. Re-reviews first, then smaller diffs.",
                                  "partial" if failures or len(rows)>25 else "checked", limit=25))
    return result


def project_page(team, cursor):
    query = """query($team:String!, $after:String){team(id:$team){projects(first:50,after:$after){
      nodes{id name url content targetDate status{type name} lead{name} members(first:20){nodes{name} pageInfo{hasNextPage}} projectMilestones(first:20){nodes{name targetDate} pageInfo{hasNextPage}}}
      pageInfo{hasNextPage endCursor}}}}"""
    return linear_query(query, {"team": team, "after": cursor})["team"]["projects"]


def projects(store, profile, today):
    team = profile.get("project_team_id")
    if not team:
        return [
            section(
                "Project deadlines and milestones",
                state="skipped",
                detail="No project team configured.",
            )
        ]
    rows = []
    cursor = None
    more = False
    for _ in range(5):
        page = project_page(team, cursor)
        rows.extend(page["nodes"])
        more = page["pageInfo"]["hasNextPage"]
        if not more:
            break
        cursor = page["pageInfo"]["endCursor"]
    old = store._record("morning_projects:" + team, {})
    current = {}
    entries = []
    files = []
    missing = 0
    for r in rows:
        status = r["status"]["type"]
        content = r["content"] or ""
        block = content.partition("## Agent Board")[2]
        deadline = re.search(r"\*\*Deadline:\*\*\s*(\d{4}-\d\d-\d\d)", block)
        target = deadline[1] if deadline else r["targetDate"]
        current[r["id"]] = dict(
            status=status, target=target, name=r["name"], url=r["url"]
        )
        changed = old and old.get(r["id"]) != current[r["id"]]
        if status in ("completed", "canceled") and not changed:
            continue
        active = status in ("started", "planned")
        days = (date.fromisoformat(target) - today).days if target else None
        details = [r["status"]["name"]]
        if active:
            if r.get("lead"):
                details.append("Lead: " + r["lead"]["name"])
            members = r.get("members", {})
            if members.get("nodes"):
                details.append(
                    "Members: " + ", ".join(m["name"] for m in members["nodes"])
                )
                more = more or members["pageInfo"]["hasNextPage"]
        if changed:
            details.append("Changed since previous scan")
        if active and days is not None:
            details.append(
                f"{target} ({abs(days)} days {'overdue' if days < 0 else 'away'})"
            )
        milestones = r["projectMilestones"]
        if active:
            details.extend(
                f"{m['name']} · {m['targetDate']}"
                for m in milestones["nodes"]
                if m["targetDate"]
                and (
                    today.weekday() == 0
                    or today.isoformat()
                    <= m["targetDate"]
                    <= (today + timedelta(days=7)).isoformat()
                )
            )
            more = more or milestones["pageInfo"]["hasNextPage"]
            for path in profile.get("project_files", {}).get(r["id"], []):
                file = Path(path)
                try:
                    with file.open() as f:
                        excerpt = f.read(4500)
                    # Treat owner-selected local files as evidence, including their
                    # rules and source lists. Never follow paths named in their text.
                    parts = re.split(r"^## (.+)$", excerpt, flags=re.M)
                    included = False
                    for heading, body in zip(parts[1::2], parts[2::2]):
                        if heading.casefold().startswith(
                            ("sources of truth", "people", "project rules")
                        ):
                            files.append(
                                item(
                                    f"{file.name} · {heading}",
                                    " ".join(body.split()),
                                    str(file),
                                )
                            )
                            included = True
                    if not included:
                        files.append(
                            item(file.name, " ".join(excerpt.split()), str(file))
                        )
                except (OSError, UnicodeError):
                    missing += 1
                    files.append(
                        item(file.name, "Configured file unavailable", r["url"])
                    )
        entries.append(item(r["name"], " · ".join(details), r["url"]))
    if not more:
        for key, value in old.items():
            if key not in current:
                entries.append(
                    item(
                        value["name"],
                        "Removed from the scoped project team",
                        value["url"],
                    )
                )
        with store.db:
            store._put_record("morning_projects:" + team, current)
    return [
        section(
            "Project deadlines and milestones",
            entries,
            "Monday project pass includes all dated milestones."
            if today.weekday() == 0
            else "Active project deadlines, upcoming milestones and paused projects.",
            "partial" if more else "checked",
            limit=35,
        ),
        section(
            "Project rules and sources of truth",
            files,
            f"{missing} unavailable. Local excerpts from configured project files.",
            "partial" if missing else "checked" if files else "skipped",
            limit=50,
        ),
    ]


async def replica_rows(config):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    # Both the restricted server binary and report are selected in private settings.
    # Source data and RPC input can never specify a statement or executable.
    binary = Path(config["server"])
    sql = config["select"].strip()
    if not binary.is_absolute() or not re.match(r"^select\s", sql, re.I) or ";" in sql:
        raise ValueError("Invalid private read report")
    cutoff = datetime.now(timezone())
    for _ in range(2):
        cutoff -= timedelta(days=1)
        while cutoff.weekday() >= 5:
            cutoff -= timedelta(days=1)
    sql = sql.replace(":two_business_days_ago", "'" + cutoff.isoformat() + "'")
    with open(os.devnull, "w") as quiet:
        async with stdio_client(
            StdioServerParameters(command=str(binary)), errlog=quiet
        ) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                result = await session.call_tool("execute_sql", {"sql": sql})
                if result.isError or len(result.content) != 1:
                    raise ValueError("Replica report unavailable")
                raw = result.content[0].text
                if len(raw) > 100000:
                    raise ValueError("Replica report too large")
                if raw.startswith("Error:"):
                    raise ValueError("Restricted replica rejected the read")
                return ast.literal_eval(raw) if raw != "No results" else []


def replica(profile):
    config = profile.get("replica_report")
    if not config:
        return section(
            "Account alerts",
            detail="No restricted replica report configured.",
            state="skipped",
        )
    rows = asyncio.run(asyncio.wait_for(replica_rows(config), timeout=50))
    if not isinstance(rows, list):
        raise ValueError("Unrecognized replica response")
    return section(
        "Account alerts",
        [item(row["title"], row["detail"], row.get("url", "")) for row in rows],
        "Unacknowledged alerts past the configured business-day threshold, grouped by account owner. Review existing asks before contacting anyone.",
    )


def standup(store, profile, today, bb_enabled):
    cid = profile.get("dailybot_checkin")
    if today.weekday() > 4 or not cid:
        return section(
            "Daily standup",
            detail="Weekend."
            if today.weekday() > 4
            else "No Dailybot check-in configured.",
            state="skipped",
        )
    status = json.loads(
        command(
            ["dailybot", "checkin", "status", "--date", today.isoformat(), "--json"]
        )
    )
    record = next(
        (
            r
            for r in status["checkins"]
            if (r.get("uuid") or r.get("followup_uuid") or r.get("id")) == cid
        ),
        None,
    )
    summary = (record or {}).get("summary") or {}
    completed = (record or {}).get(
        "response_completed", summary.get("current_user_has_response")
    )
    if record is None or completed is True:
        return section(
            "Daily standup",
            detail="Already completed."
            if record
            else "No matching check-in scheduled today.",
            state="skipped",
        )
    if summary.get("current_user_due_on_date") is False:
        return section(
            "Daily standup", detail="Check-in is not due today.", state="skipped"
        )
    if completed is not False:
        raise ValueError("Unknown check-in status")
    data = {s["label"]: s["items"] for s in brief(store, bb_enabled)}
    pending = json.loads(command(["dailybot", "checkin", "list", "--json"]))[
        "pending_checkins"
    ]
    own = next((r for r in pending if r.get("followup_uuid") == cid), None)
    if not own:
        return section(
            "Daily standup",
            detail="No pending personal response. Refresh status before submitting.",
            state="partial",
        )
    question = next(
        (
            q.get("question", "")
            for q in own.get("template_questions", [])
            if q.get("index") == 0
        ),
        "",
    )
    entries = (
        [item("Previous workday prompt", question, record.get("url", ""))]
        if question
        else []
    )
    for key, label, limit in [
        ("Completed since previous workday", "Completed", 3),
        ("Carryover", "Carried over", 2),
        ("Suggested actions", "Today", 3),
    ]:
        entries.extend(
            item(label, r["title"], r["url"], r["work_id"]) for r in data[key][:limit]
        )
    return section(
        "Daily standup",
        entries,
        "Draft for review. Confirm prior-workday completions, carryover and whether deadlines are on track before submitting.",
    )


def run_step(store, step, bb_enabled=False):
    from settings import SOURCES

    if step == "bb":
        coverage = source_check(store, step, bb_enabled)
        try:
            queue = external_queue(ConfigStore().read("morning") or {})
        except Exception:
            queue = section(
                "Additional agent queue",
                detail="Configured queue could not be read.",
                state="failed",
            )
        return [coverage, queue]
    if step in SOURCES:
        return [source_check(store, step, bb_enabled)]
    if step == "brief":
        return brief(store, bb_enabled)
    profile = ConfigStore().read("morning") or {}
    today = datetime.now(timezone()).date()
    if step == "checkout":
        return [checkout(profile)]
    if step == "alerts":
        return [replica(profile)]
    if step == "standup":
        return [standup(store, profile, today, bb_enabled)]
    if step in ("projects", "cadence", "inbox", "prs"):
        source = "github" if step == "prs" else "linear"
        if source not in selected_sources():
            return [
                section(
                    {
                        "projects": "Project deadlines",
                        "cadence": "Recurring work and constraints",
                        "prs": "Pull requests",
                        "inbox": "Linear notifications",
                    }[step],
                    state="skipped",
                    detail=source + " is off.",
                )
            ]
        if step == "cadence":
            return [cadence(profile)]
        if step == "projects":
            return projects(store, profile, today)
        if step == "prs":
            return pull_requests(store, profile)
        bind_linear()
        lines = command(["bb", "linear", "inbox"]).strip().splitlines()
        return [
            section(
                "Linear notifications",
                [item(line.strip()) for line in lines[1:]],
                lines[0] if lines else "No notifications returned.",
            )
        ]
    raise ValueError("Unknown morning check")


def cadence(profile):
    documents = profile.get("planning_documents", [])
    if not documents:
        return section(
            "Recurring work and constraints",
            state="skipped",
            detail="No planning documents configured.",
        )
    entries = []
    for doc in documents:
        data = linear_query(
            "query($id:String!){document(id:$id){title content url updatedAt}}",
            {"id": doc},
        )["document"]
        lines = [line.strip() for line in data["content"].splitlines() if line.strip()]
        entries.append(item(data["title"], " ".join(lines[:12]), data["url"]))
    return section(
        "Recurring work and constraints",
        entries,
        "Read-only planning references. Check the linked calendar for each recurrence.",
    )


def linear_query(query, variables):
    key = os.environ.get("LINEAR_API_KEY") or (
        ConfigStore().read("linear_credentials") or {}
    ).get("api_key")
    if not key:
        raise ValueError("Configure Linear project read access")
    response = httpx.post(
        "https://api.linear.app/graphql",
        headers={"Authorization": key},
        json={"query": query, "variables": variables},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise ValueError("Linear planning read failed")
    return payload["data"]


def external_queue(profile):
    queue = profile.get("paperclip")
    if not queue:
        return section(
            "Additional agent queue",
            detail="No secondary work queue configured.",
            state="skipped",
        )
    args = [
        "paperclipai",
        "issue",
        "list",
        "--company-id",
        queue["company"],
        "--project-id",
        queue["project"],
        "--assignee-agent-id",
        queue["agent"],
        "--status",
        "todo,in_progress,in_review,blocked",
        "--json",
    ]
    rows = json.loads(command(args))
    return section(
        "Additional agent queue",
        [
            item(
                row["title"],
                row["identifier"] + " · " + row["status"],
                row.get("url") or "",
            )
            for row in rows
        ],
        "Open assignments for the configured project and agent. Blocked assignments need review.",
    )
