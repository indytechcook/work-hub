# Automatic context and completion review

Work Hub now links new BB conversations to work and checks commitments against related evidence. These features use the existing SQLite ledger, configured read connectors and selected Claude, Codex or Gemini analyzer. They require no public ports or additional hosted service.

## Conversation context

Enable `autoContext` in the Work Hub plugin settings, alongside the existing `collectThreads` opt-in, then reload the plugin. Both settings default off on a new installation.

```sh
bb plugin config work-hub set autoContext true
bb plugin reload work-hub
```

When a user starts a conversation or adds a reference, the dispatch hook:

1. Preserves explicit Work Hub composer metadata and manual associations.
2. Matches canonical Slack, GitHub, Linear and BB links to captured work. Workspace/repository identities are part of the match; it does not match every message in a Slack channel as one task.
3. Uses one unambiguous active match, or the parent thread's work. Otherwise it creates a work item immediately. Ambiguous candidates are included in the agent context for review. Explicit repeat requests with resolved prior obligations get a new item and retain prior work references.
4. Loads referenced sources using existing configured, read-only connections. Disabled sources and wrong Slack/Linear workspaces fail closed. Exact links are supported; bare ticket/PR numbers and semantic title matching do not automatically associate work.
5. Supplies a bounded context packet to new provider sessions, and exposes `work_hub_context` to refresh it during the conversation. The sidebar uses the same saved associations and reports pending/failed source reads.

The first dispatch may wait up to about twelve seconds for source context. A slow or unavailable connector does not indefinitely block work; pending reads retry in the background. Hooks cannot rewrite a user's prompt, and BB does not replace instructions in a running provider session. Existing sessions need a provider restart to discover newly registered tools; the sidebar and `contextPacket` RPC still work immediately. Changes during an established session are retrieved with `work_hub_context`, not silently injected into the model's transcript.

Context jobs survive restart. Duplicate dispatch attempts do not create duplicate work or fetch jobs. Related source evidence can belong to multiple work items. Targeted reads never advance scheduled collection checkpoints. Source context is untrusted data and does not authorize performing the request it describes.

## Completion review

Use **Needs attention → Check completion**, including from the portrait attention view's expanded list, or **Actions → Check completion** for a commitment. The review screen offers:

- Checks for all unseen or all open commitments, independent of the bounded dashboard snapshot.
- Paginated assessments: confirmed complete, partly complete, still open, upcoming, superseded and unconfirmed.
- Exact evidence excerpts and sidebar source links, assessment time, queue/retry counts and shared daily allowance.
- Resolve verified, resolve confirmed items on the current page, check again, and explicit manual Open/Fulfilled/Cancelled controls.

The worker fetches bounded current reads for explicit GitHub, Slack, Linear and BB references, sharing recent reads between checks. Other sources contribute captured evidence through existing work associations; they do not yet have targeted readers. It evaluates the specific promise, not simply whether a parent issue or PR closed. Model output must cite exact quotes from known evidence. Confirmations need newer evidence, high confidence and no requested-read failures. Any changed commitment or evidence invalidates application of a saved recommendation. Uncertainty remains open.

The engine uses the explicitly configured isolated analyzer; there is no model fallback and source text cannot select tools or commands. A review currently uses one model call per commitment when its evidence changed. Source reads and context linking do not consume model calls. Repeated unchanged assessments use the cached verdict, although the worker conservatively reserves a daily attempt before processing. Large backlogs can span multiple days under a small allowance; the UI reports this rather than implying completion.

## Background policy

The encrypted `policy` record accepts these additional switches:

```json
{
  "max_analyses_per_day": 120,
  "completion_review_enabled": true,
  "auto_resolve_confirmed": false
}
```

Preserve existing budget/catch-up fields when updating the whole policy record. Read settings privately; never print credentials. Automatic review queues a bounded sweep of open commitments, rechecks assessed items after six hours, and requeues affected assessments when new referenced evidence arrives. Manual review requests work regardless of the automatic-sweep switch. Completion checks and normal extraction alternate within the existing daily budget. Budget exhaustion leaves checks queued until the UTC reset; changing the budget is an explicit configuration choice.

Automatic resolution defaults off. If explicitly enabled, only current confirmed-complete assessments can fulfill a local commitment. Work status, focus, seen state, linked PR/issue status and external messages remain separate. Every assessment is saved to a local audit table; reopening is a version-checked manual operation.

## Portability and verification

Ledger export schema 3 includes reference identities, context jobs, review state and assessment history. Schemas 1 and 2 still import; interrupted jobs recover conservatively. Credentials remain in the configured storage backend, outside the ledger export.

Synthetic tests cover repeat requests, exact identity matching, manual association preservation, full inventories, source failures, stale evidence, version conflicts, retry leases, checkpoint preservation and dispatch wait bounds. BB's dispatch hook remains an experimental SDK API. Source coverage and model interpretation remain imperfect: read the evidence when deciding whether a commitment is fulfilled.


## Immediate return points and tracking ownership

With BB collection enabled, Work Hub captures idle, failed and pending-interaction states immediately, with periodic reconciliation for missed events. Final reports are labeled **captured BB state**, not verified completion. Agents can use `work_hub_checkpoint` for structured progress, remaining work, next action, blockers and evidence. These writes require no background analyzer budget. The latest return point appears in work context, the resume picker and new-agent context. Blocked, failed, input-needed and review-ready reports can enter attention; routine idle reports do not.

Chosen intent and commitments remain separate. Checkpoints cannot close work or promises. Old reports cannot overwrite a newer turn, and repeated captures do not create repeat alerts. Collection retains its existing seven-day/unarchived scope; events during downtime outside that scope are not guaranteed to be recovered.

Personal tracking belongs in Work Hub; team issues and required change records remain in Linear. To retire a duplicate tracking team, first stop its writer/automation in the owning service and preserve a private ledger backup. Then invoke the local stdio API with the team's actual key (the example `TRACK` is synthetic):

```bash
printf '%s' '{"op":"configure_tracking","args":{"teams":["TRACK"]}}' | .venv/bin/python hub/cli.py
```

This stores portable tracking-role policy in the ledger. It does **not** pause external automations. Captured Linear records from those teams remain readable but skip new extraction, and cannot supply automatic source-match candidates. Existing unresolved commitments move to the visible **old tracking board** reconciliation queue, with a count of past due dates. No obligation or external issue is deleted or closed. Choose **Keep for follow-up** to retain a real commitment in normal attention/reminder/review behavior, or review its evidence and explicitly resolve/cancel a duplicate. Keeping does not confirm an ambiguous commitment's ownership.

To reverse the policy, supply an empty `teams` list. Pending latest reference-only receipts become eligible for analysis again; previous data and choices remain. Never retire a real team merely because its issues are also linked from Slack.


## Recognizing Slack conversations

Slack labels use captured parent-message authors, UTC dates/times and a bounded message excerpt throughout work lists, association selectors, thread context and source links. Association and work search also match captured Slack authors/message text. These are display labels: original titles, source URLs, work IDs, associations and commitment state remain unchanged. Missing previews are labeled unavailable; thread timestamps provide a fallback when known. Custom work titles are preserved. No additional Slack request or model call is needed.
