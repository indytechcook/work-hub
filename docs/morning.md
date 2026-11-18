# Start morning

Choose **Start morning** in Work Hub. BB opens a visible planning conversation in the configured Work Hub project, using that project's provider, model and default environment. If no Work Hub project is configured, the dashboard uses the selected BB project. Each local date gets a thread. Clicking again opens it, including after a plugin reload. An archived planning thread can be replaced.

The conversation reports progress while the local checks run. It can show provisional priorities from the saved evidence during a long scan, then proposes three actions with refreshed links. Choose, change or reorder them in the thread. It saves the plan after confirmation. **Edit today’s plan** opens the dashboard editor. Both use the same date, ordered work IDs, note and optimistic version check.

## Checks and order

| Stage | Evidence and conditions |
| --- | --- |
| Read current work | Read source permissions, chosen actions, current and previous plans, focus, unresolved commitments, return points and outstanding analysis. Retired tracking teams are excluded from ordinary commitments, except commitments explicitly kept during reconciliation. |
| Check the work environment | Inspect the configured checkout branch, worktrees, uncommitted changes, divergence from last fetched upstreams and optional full-slot count. Branch changes and cleanup require a separate action with worktree safety checks. |
| Check project dates | For a configured Linear project team, page projects, distinguish active, paused and completed, compare the last successful scan, count down to explicit deadlines or target dates, and read upcoming milestones. On Mondays include all available dated milestones. Read configured local project rules, people and source references for active projects, plus configured cadence and constraints documents. |
| Check shared sources | Use existing opt-ins for BB, Gmail, Slack, Calendar, Drive, Linear, GitHub, meeting notes and Better Stack. Reuse successful external scans less than ten minutes old. Otherwise run the existing locked collector. Display the last successful scan, partial results, contention, disabled sources and failures. |
| Check incoming work | Read the bound Linear inbox, open authored PRs and requested reviews. Include drafts, review decisions, latest review or inline feedback, CI and optionally the configured secondary agent queue. Optional restricted replica reporting groups unacknowledged account alerts by owner. |
| Verify linked work | Check selected original Slack threads, PRs and Linear issues for prioritized work, follow-ups and waiting items. Reuse short-lived checked receipts. The mail collector covers existing Gmail obligations. Verify all eligible original links in batches with visible counts, one sequential retry of failed reads, and individual outcomes. |
| Prepare the brief | Rank overdue personal commitments first. Include the three oldest overdue commitments and total, approvals, calendar and RSVP status, the next seven days, follow-up review dates, stale waits, later captured Slack messages from the same person, running conversations, carryover, recent completions and arrivals. The planning agent reconciles these with live reads and asks one decision at a time. |
| Prepare a standup | On weekdays, check the configured Dailybot questionnaire for the current user. For a pending response, provide the previous-workday prompt, completed commitments, carryover and today's proposed priorities. A completed or unscheduled check-in is skipped. Review the draft in the thread. |

## Optional private inputs

Ordinary sources use the existing Work Hub configuration and credentials. The optional `morning` record uses the same private storage as other configuration. Set it via `hub/configure.py set morning --input-file /absolute/private/morning.json`. Every field is optional.

| Field | Meaning |
| --- | --- |
| `repository` | An owner/repository scope for the two live GitHub review lists. |
| `checkout`, `base_branch` | Absolute checkout path and expected main branch. Optional `slots` object with `port_key`, numeric `base`, `stride` and `capacity` for installations that allocate worktree ports. |
| `project_team_id` | The explicitly selected Linear team for project metadata. Requires the Linear source and a Linear API key available to the plugin host via `LINEAR_API_KEY` or the separate private `linear_credentials` record with `api_key`. |
| `project_files` | Mapping of Linear project IDs to explicit local text file paths. Source-checklist excerpts from active projects can be supplied to the interactive thread. |
| `planning_documents` | Linear document IDs for cadence and constraints. |
| `paperclip` | Optional secondary queue with `company`, `project` and `agent` IDs. Requires the configured Paperclip CLI. |
| `dailybot_checkin` | The owner's check-in ID. Requires the authenticated Dailybot CLI on the plugin host. |
| `replica_report` | Optional administrator-configured `server` (absolute path to a trusted restricted Postgres MCP launcher) and `select` (reviewed single read returning `title`, `detail` and optional HTTPS `url` columns). The optional `:two_business_days_ago` placeholder uses the configured timezone and skips weekends. The server must enforce restricted mode and use a read-only database role. No program, SQL or path is accepted from a thread or source excerpt. |

The active interactive provider receives the evidence the thread requests. Background extraction keeps its own provider choice, permissions and budget. Progress lives in the plugin process. The latest finished brief, daily thread identity, confirmed plans and project comparison state live in the local ledger. Reloading during a run releases the worker and the morning thread can request a fresh run.

## Coverage limits

Source windows and caps still apply. Drive supplies metadata, meeting notes can be summaries, and the Better Stack collector covers error patterns. Incident and heartbeat coverage requires a separate adapter. Each original-link pass takes a snapshot of eligible links. Three workers check four links per batch. The thread can page through long results. PR feedback covers recent reviews, conversation comments and the ten most recently updated inline comments. Resolving review threads, project-specific systems outside the configured reads, and older correspondence can require a manual check. A missing reply or progress report cannot establish completion.

The morning thread prepares decisions and drafts. Sending messages or standups, launching review or fix agents, merging or deploying, changing a checkout and removing worktrees are separate actions. Retired tracking issues, agent-run mirrors, and listener triggers are excluded from the routine.

The original command proceeds from refresh and briefing to dispatch, human decisions and close-out. Work Hub opens the interactive thread at refresh, guides the decisions, and uses its editable plan and return notes for personal tracking. The weekday standup is prepared during the checks and reviewed after priorities are chosen. The existing **Finish day** action remains available for close-out.
