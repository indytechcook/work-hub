# Local Work Hub core

See [SETUP.md](../SETUP.md) for installation, accounts, model selection and background jobs. The core is independent of BB and stores private data in `.state/work.db`.

## Components

- `store.py`: SQLite receipts, checkpoints, work, conversations, obligations, evidence, audit changes, and job records.
- `presentation.py`: next-action choices, day plans, attention acknowledgement and context projections.
- `cli.py`: fixed JSON operations over stdin/stdout. No listening API port.
- `settings.py`: private identity, timezone, source opt-ins and account settings.
- `collect.py`: outbound source polling. No enabled sources means no collection.
- `analyze.py`, `analyzer_runtime.py`, `worker.py`: explicit Claude/Codex provider selection, structured extraction, leased work and daily limits.
- `slack_auth.py`, `mcp_login.py`, `mcp_access.py`: direct local sign-in and bounded read-only client operations.
- `install_local.py`: selected-source collection and analysis through macOS LaunchAgents. `--dry-run` previews commands without installation.
- `../connectors/google`: standard Google API adapter and optional read-only stdio MCP tools.
- `../bb-plugin-work-hub`: BB interface, conversation capture and optional reminders.

## Coverage

| Source | Current bounded coverage |
|---|---|
| BB | Unarchived threads across projects updated within seven days, including hidden threads; latest three prompts and latest assistant output |
| Gmail | Initial seven days, then overlapping inbox/sent reads; plain text, no attachments |
| Calendar | Primary calendar, previous seven days and next thirty days, including cancellations |
| Drive | Changed file metadata only; no document bodies/comments and no filename-derived obligations |
| Linear | Open assigned issues in bound teams; known open follow-ups reread after closure; recent comments |
| GitHub | Authored/review-requested open PR descriptions; not full review/comment history |
| Slack | DMs/group DMs, your messages and mentions with thread replies; no file contents |
| Granola | Initial seven days of notes/summaries, then updated notes; not full transcripts |
| Better Stack | Configured application/environment, patterns seen over seven days; observation only |

Check each source's scope, successful-read timestamp, errors and analysis backlog. An empty follow-up list is not evidence that nothing is owed. Summaries and inferred commitments need human review.

## Persistence and export

Conversation associations live in `conversation_work_links`, combined with primary work references through `work_memberships`. Shared commitments retain one identity/version. Fulfillment from any associated view updates the same record.

Source checkpoints advance only after durable receipt writes. Failures retain prior checkpoints. Leases recover interrupted analysis; source-version checks reject stale model output. Reminder outcomes that cannot be confirmed remain uncertain rather than being blindly resent.

The stdio API accepts `{"op":"export"}`. Its output contains private source excerpts and must be stored privately. `{"op":"import_snapshot","args":{"payload":...}}` imports into an empty destination only, validates table shapes/references, holds unfinished agent jobs and marks in-flight reminders uncertain. Credentials must be reconnected separately. Import/export is a trusted local operation, not a public upload endpoint.

Use SQLite's backup API for a running ledger, or stop all writers for a coordinated file backup. Copying only the `.db` file while WAL writes are active is not a complete backup.

## Tests

```bash
.venv/bin/python -m unittest discover -s hub -p 'test_*.py'
.venv/bin/python -m unittest discover -s connectors/google -p 'test_*.py'
```

Tests use synthetic traffic and temporary state. Provider qualification makes actual model calls with synthetic input and is deliberately separate from unit tests. Live unattended coding workers are not included in this version.
