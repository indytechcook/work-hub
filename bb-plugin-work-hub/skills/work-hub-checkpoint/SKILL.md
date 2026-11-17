---
name: work-hub-checkpoint
description: "Save a Work Hub return point when asked to checkpoint progress, switch tasks, hand off context or finish the day. Preserves the chosen next action and current conversation link; not an automatic request to mark work or commitments complete."
---
# Save a return point

1. Identify the current thread with `bb status --json` and its work with `threadContext`, or locate the item the user named. If several associations make the destination ambiguous, ask which item to update; prepare the checkpoint text while waiting. Never save to a guessed item.
2. Read `workDetail`. Compare current chosen intent, the conversation-specific return point, source timestamps and actual work performed. Write a compact checkpoint: verified progress, what remains, the concrete next step, a blocker/decision if any, and useful evidence/artifact links. Separate a plan from a result and mention unrun checks honestly.
3. An explicit request to save a checkpoint authorizes updating the local chosen next action. Call `saveIntent` with the fetched version, a concise `next_action` (at most 1500 characters), and the existing `purpose`/`project` unchanged unless the user asked to change them. Retain the preferred thread unless the requested handoff makes the current **already-associated** thread the intended return point.
4. Read the saved intent back. Report it as saved only after successful readback. On a version conflict, reload and reconcile with the newer choices; do not erase another editor's update.
5. Put the fuller narrative checkpoint in the conversation's final response. The currently implemented immediate write is the chosen next-action field, not a separate narrative checkpoint table. BB reconciliation and asynchronous analysis capture the conversation later. Do not claim that a new analyzed summary is already current, and do not repeatedly sync hoping to capture a final response not yet sent.
6. Leave work status, focus, attention acknowledgments and obligations unchanged unless the user requested those changes. Finished coding or ending the day is not proof of fulfillment, merge, deployment or production verification.

## Supported operations

Call `bb plugin rpc call work-hub METHOD --input-file FILE --json` with structured input in a private temporary file (600), then delete it. Never interpolate private/source text into shell command strings.

- `threadContext`: `{"threadId":"actual-thread-id"}`; optional `workId` selects one existing association.
- `workDetail`: `{"workId":"actual-work-id"}`.
- `saveIntent`: `{"workId":"actual-work-id","version":2,"next_action":"Concrete next step; relevant blocker if any","purpose":"preserved value","project":"preserved value","thread_id":null}`. Use the fetched version and current fields. `thread_id` is either null or an already-associated BB thread ID.

If the thread is not captured/associated, do not fabricate IDs. Reconcile opted-in BB capture, then use the Work Hub association controls for an explicitly chosen work item. If capturing is disabled or the association cannot be established, provide the checkpoint in the thread and clearly say it has not been saved to a Work Hub item.

The operation preserves a portable ledger choice independent of model memory. It does not change the original transcript or send external messages. No direct SQL, invented save-summary endpoint, automatic source changes, or model-specific identity assumptions.
