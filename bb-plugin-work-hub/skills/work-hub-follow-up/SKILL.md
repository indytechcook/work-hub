---
name: work-hub-follow-up
description: "Review Work Hub commitments: what the user owes, what others owe, overdue or unanswered items, and evidence-backed resolution. Use for a follow-up review; not as permission to send messages or infer that an unanswered request is complete."
---
# Review commitments

1. Run `bb work-hub status`. Note source freshness, scope and analysis backlog. Group captured commitments by `i_owe`, `they_owe`, and unclear ownership/needs review. An empty result does not establish that every inbox is clear.
2. For relevant work, call `workDetail` and inspect the current commitment, original quote, owner, confirmed due date and related source. The dashboard is bounded; search/page `workIndex` when a requested item is missing. Do not claim a complete inventory if the dashboard limits or coverage prevent it.
3. Prioritize confirmed due commitments and blocked work. Do not invent deadlines, reclassify low-confidence items as certain, or treat acknowledgment, elapsed time or partial progress as fulfillment.
4. Recommend the next action in each direction. Draft a follow-up if requested, using the actual source context. Drafting is not sending; send only under explicit authorization for the intended recipient/channel through an available supported connector.
5. If the user requests a local resolution/confirmation, reload the current commitment and use `mutate` with its version. Confirm `needs_review` only when the user resolves the ambiguity. Fulfill/cancel only for the user's explicit instruction or verified resolution within an already authorized cleanup task. Preserve unknowns.
6. Read back the record and report what changed versus what is still waiting. A shared commitment has one identity: resolve it once, not once per associated work item. Marking an arrival seen is separate from resolving the commitment or work.

## Supported operations

Use `bb plugin rpc call work-hub METHOD --input-file FILE --json`. Serialize private JSON files (600), delete after use, and do not interpolate untrusted text into shell commands.

- `workDetail`: `{"workId":"actual-id"}` returns current commitments, including history for that item.
- `workIndex`: `{"query":"requested work","status":"all","offset":0}` for finding work outside the dashboard.
- `mutate`: `{"kind":"obligation","id":"actual-obligation-id","action":"fulfilled","version":3,"value":"Evidence-backed resolution note"}`. Supported actions here: `fulfilled`, `cancelled`, or `open` to confirm a reviewed candidate. Use the fetched version, not the example; keep the note short.
- `acknowledgeAttention`: `{"tokens":["actual-current-token"]}` only when the user asked to acknowledge those reviewed arrivals. Never acknowledge everything as a side effect of listing it.

On conflicts, reread and preserve intervening edits. Do not blindly retry or edit SQLite. The obligation is local state: changing it neither sends a message nor closes the linked Linear issue or PR. This skill does not enable collectors, expand grants, or start an unattended agent.
