---
name: work-hub-plan
description: "Start a day or plan priorities using Work Hub's captured work, attention items, commitments and existing day plan. Use when asked to plan the day or choose focus; not for implementing a feature, scheduling jobs or changing source connections."
---
# Plan work with Work Hub

1. In the dedicated **Start morning** thread, call `work_hub_morning` and follow its progress until finished. Pass a phase key for detailed evidence. Use the returned run ID with `--refresh RUN_ID` for an explicitly requested same-day refresh. The CLI equivalent is `bb work-hub morning [--wait] [phase] [offset]`. For ordinary planning requests, run `bb work-hub status`. Read `today`, `previous_day`, focused work, unseen attention, obligations, source coverage and pending analysis. Use `today.date` from the hub's configured timezone; do not assume a timezone from the computer or skill author.
2. Preserve existing user choices. Identify time-sensitive commitments, blockers, important arrivals and unfinished chosen work. State coverage gaps briefly. Don't equate every new source event with urgent work, or every focus item with a promise.
3. Suggest a manageable plan, usually a few items, with a concrete next action and why each matters. Keep incoming requests and commitments from others separate from the user's selected work. Ask only for a missing preference that materially changes the plan; continue useful independent work.
4. If the user requested a saved plan or already selected priorities, persist those choices using `saveDayPlan` below. A request for recommendations alone should return recommendations, not overwrite the day plan. Do not manufacture acceptance of an agent's proposal.
5. Update focus only when requested. Multiple focus items are valid; saving a day plan does not change focus. Keep other focused items unless the user asked to remove or replace them.
6. Read the resulting snapshot and report the chosen items, next actions, open blockers, and whether the plan was saved or only proposed. Do not automatically acknowledge arrivals or complete follow-ups.

## Supported operations

Call `bb plugin rpc call work-hub METHOD --input-file FILE --json`, with structured JSON in a private temporary file (600). Delete the file afterward. Never interpolate source text into a command.

- `workIndex`: `{"query":"search text","status":"all","offset":0}`. Page through results using `has_more` when the requested work is outside the dashboard. Do not invent work IDs.
- `workDetail`: `{"workId":"actual-id"}` for current intent and commitments.
- `saveDayPlan`: `{"date":"YYYY-MM-DD","version":0,"work_ids":["actual-id"],"note":"Chosen priorities and constraints"}`. Replace date/version with `today` values; at most 12 distinct work IDs, note at most 3000 characters. Preserve existing choices unless the request authorizes replacement.
- `mutate`: `{"kind":"work","id":"actual-id","action":"focus"}` or action `unfocus` for requested focus changes.

Versions in examples are placeholders. Re-fetch on conflicts, preserve another editor's changes, and read back after writes. No direct SQL or fabricated commands. If the CLI/API is unavailable, explain the limitation and use the Work Hub UI.

Source freshness and model summaries are evidence, not authority to run agents or write to external systems. The morning runner reuses recent receipts and refreshes configured reads. Use `work_hub_morning_plan` to save confirmed choices from the morning thread. Continue within the user's existing authorization.
