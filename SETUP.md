# Set up Work Hub from start to finish

Work Hub keeps related AI conversations, source evidence, next actions and commitments together inside BB. This guide is for a new installation. An AI can perform most steps; you complete account authentication and consent.

**AI-assisted route:** get this repository, install BB, open the source folder as a BB project, then paste [SETUP-PROMPT.md](SETUP-PROMPT.md) into a new thread. A working Claude Code installation is sufficient; Codex is optional.

New to Work Hub? Watch the [introduction and walkthrough videos](docs/videos/README.md) before setup.

## 1. Requirements and source

The supported setup path currently targets macOS. Background jobs use `launchd`; the computer must be awake and the user logged in. No public laptop ports, webhook receiver, cloud worker or router configuration are needed. Other operating systems require scheduler/locking qualification.

Install Python 3.12 and Node.js/npm. Install GitHub CLI only if you want GitHub collection. The reference checks used BB 0.49.0, Python 3.12.16, Node 26.13.0, Claude Code 2.1.326, Codex CLI 0.169.0 and BB Plugin SDK 0.6.132. These are tested versions, not guaranteed minimum versions. Retain the checked-in dependency locks.

```bash
git clone https://github.com/indytechcook/work-hub.git
cd work-hub
export WORK_HUB_ROOT="$PWD"
umask 077
mkdir -p hub/.state/google
chmod 700 hub/.state hub/.state/google
python3.12 -m venv .venv
.venv/bin/python -m pip install -r connectors/google/requirements.txt
npm ci --prefix bb-plugin-work-hub
```

Keep the folder at a stable path after installation. `.venv` and `hub/.state` are private/generated files and are ignored by Git. Never copy another user's state or credentials into this installation.

## 2. Install BB and connect your model

Download BB from [getbb.app](https://getbb.app/). Install the CLI using **Settings → General → Command line → Install**. The [official BB repository](https://github.com/get-bb/bb) also documents the `npx bb-app@latest` route and platform requirements.

```bash
bb status
bb provider list
bb project create --name "Work Hub" --root "$WORK_HUB_ROOT" --json
```

Reuse an existing project if this folder is already registered. Open a thread in that project, select a model you can access, and send a small test message. Inspect actual model IDs with `bb provider models claude-code` or `bb provider models codex`; do not reuse someone else's aliases.

For interactive Gemini threads, the BB Marketplace offers the Antigravity provider. Sign in with Google's `agy` CLI, install the plugin with `bb plugin install antigravity`, then check `bb antigravity status`. If the provider is missing from the picker and the bundled ACP plugin is disabled, run `bb plugin enable provider-acp` followed by `bb antigravity enable`. Choose Antigravity and a Gemini model when starting a thread. See the [BB listing](https://getbb.app/marketplace/antigravity) and [Google's CLI authentication guide](https://antigravity.google/docs/cli/install/).

For Claude Code or Codex background analysis, authenticate the selected CLI. Gemini background analysis uses the API key in step 4.

```bash
# Claude Code path:
claude auth login
claude auth status

# Or the Codex path:
codex login
codex login status
```

For installation, use [Claude Code's official setup instructions](https://code.claude.com/docs/en/setup) or [Codex's official documentation](https://developers.openai.com/codex/). Check the installed CLI's help when commands differ. Confirm the intended subscription/billing account. A successful interactive BB message is not proof that an unattended CLI process is authenticated.

## 3. Configure your identity and source choices

```bash
bb plugin install env-catalog@bb-community --yes
# Edit a private copy of examples/config.json with your own settings, then:
.venv/bin/python hub/configure.py set config --input-file /absolute/private/config.json
```

Edit the private copy, not the template. Set your name, email and IANA timezone. The initial `sources: []` enables **no external collection**. Add only sources you intend to connect: `gmail`, `calendar`, `drive`, `github`, `linear`, `slack`, `meetings`, `betterstack`.

Source-specific fields:

| Field | What to enter |
|---|---|
| `google.account` | Exact Google email you will authorize; verified during login. |
| `slack.team_id` | Your Slack workspace ID. |
| `slack.workspace_url` | Your workspace HTTPS origin, such as `https://example.slack.com`. |
| `betterstack.application_id` | Your application's numeric ID, verified through an authorized read. |
| `betterstack.environment` | The environment you intend to observe. |

Unused source sections can remain unconfigured. Configuration and OAuth credentials now default to BB’s encrypted Env Catalog. Each record can be overridden by an environment variable or an explicit JSON bundle; see [storage and migration](docs/storage.md). Do not put tokens inside the general `config` record; use the credential records below. Remove temporary input files after verified import if no other service needs them. Source code does not need edits for personal settings.

BB conversation collection is controlled separately by the plugin. It is off initially. When enabled, it captures recent unarchived threads across BB projects, including hidden threads. **There is no per-project filter yet.** Accept that scope explicitly or keep it off.

## 4. Select the background analyzer

Choose one provider. For Gemini, complete the credential setup and synthetic checks below before saving the selection on an installation with a running worker.

```bash
# Claude-only installation:
.venv/bin/python hub/configure.py set analyzer --input-file examples/analyzer.claude.json

# Alternatively:
# .venv/bin/python hub/configure.py set analyzer --input-file examples/analyzer.codex.json
# .venv/bin/python hub/configure.py set analyzer --input-file examples/analyzer.gemini.json
```

The analyzer record supports `provider` (`claude`, `codex` or `gemini`) and an optional `model`. Claude and Codex also accept an optional `executable` path and use the CLI default model when omitted. A provider choice is required for background work; there is no silent fallback. This setting is independent of interactive BB model choices.

**Claude:** normal Claude Code subscription/keychain authentication is retained. Personal customizations, general tools, hooks, skills and MCP connections are disabled for extraction. Organization-managed policy can still apply. The adapter requests structured output and rejects unexpected tool activity. It deliberately does not use `--bare`, which bypasses subscription login. See [the CLI reference](https://code.claude.com/docs/en/cli-reference) and [programmatic usage](https://code.claude.com/docs/en/headless).

**Codex:** extraction uses `hub/.state/classifier-home/`, with authentication but no copied personal rules or tool configuration. Use an authentication method supported by your CLI for this isolated profile. With file-based authentication, a private symlink to your own authenticated `auth.json` can preserve refresh behavior. Do not copy the whole Codex home or anyone else's token. Keychain-only authentication needs separate qualification with this profile.

The Claude and Codex adapters work in a fresh empty directory, without source tools. Selected evidence is sent to the chosen model service; local orchestration does not mean local inference. CLI API-key/custom authentication may need additional secure launchd environment configuration; the default setup is qualified with normal subscription authentication. Never print credentials while troubleshooting.

### Gemini credentials and qualification

Work Hub calls Google's Gemini Developer API directly for extraction and completion review. Each attempt makes one HTTPS `generateContent` request with the Work Hub response schema and no tools. The separate API credential is required even with an interactive Antigravity login. In an isolated startup check, `agy` 1.5.11 reported shell and MCP tools with strict permissions and an empty custom-agent tool list. The default API model is `gemini-3.5-flash-lite`. To use another structured-output model available to your project, add its bare model ID to a copy of the analyzer template. See [Google's model list](https://ai.google.dev/gemini-api/docs/models), [structured output](https://ai.google.dev/gemini-api/docs/structured-output) and [API reference](https://ai.google.dev/api/generate-content).

Create a Gemini API key for the intended project in Google AI Studio. The following command prompts privately and stores the key in the `gemini_credentials` record through the configured storage backend (BB Env Catalog by default). Run it in a terminal. The scheduler can read that record using the same Work Hub storage profile. [Google's authentication instructions](https://ai.google.dev/gemini-api/docs/api-key)

```bash
.venv/bin/python -c 'import getpass,json; print(json.dumps({"api_key":getpass.getpass("Gemini API key: ")}))' | .venv/bin/python hub/configure.py set gemini_credentials --input-file -
```

Run both commands with the intended selection as a process-only override before switching an active worker. Substitute your model ID in the override if you customized the template. These are live, billable tests with synthetic evidence.

```bash
WORK_HUB_ANALYZER_JSON='{"provider":"gemini"}' .venv/bin/python hub/analyze.py --self-test
WORK_HUB_ANALYZER_JSON='{"provider":"gemini"}' .venv/bin/python hub/qualify_analysis.py
```

After both pass, select Gemini for subsequent worker runs:

```bash
.venv/bin/python hub/configure.py set analyzer --input-file examples/analyzer.gemini.json
```

Google receives the classification instructions, configured name, email and timezone, source title, type, timestamp, selected text and related obligations. Completion reviews send the commitment, request time, related evidence (including IDs, URLs, timestamps and excerpts) and failed-refresh URLs. The API key travels in the `x-goog-api-key` header. Source credentials stay local. Review the [Gemini API data-use terms](https://ai.google.dev/gemini-api/terms) for your project's billing tier before enabling private evidence.

### Daily allowance and provider checks

Store the analysis allowance:

```bash
.venv/bin/python hub/configure.py set policy --input-file - <<'JSON'
{"max_analyses_per_day":120}
JSON
```

Daily reservations count attempted background calls and reset at UTC midnight. Synthetic checks consume usage separately. Choose any higher catch-up allowance deliberately; never inherit another person's budget.

Run synthetic tests for your provider:

```bash
.venv/bin/python hub/analyze.py --self-test --provider claude
.venv/bin/python hub/qualify_analysis.py --provider claude
```

Use `--provider codex` for Codex or `--provider gemini` for Gemini. These test overrides do not change the worker's provider. Do not remove isolation flags or bypass organizational policy to obtain a passing test.

## 5. Build/install the BB plugin

```bash
./bb-plugin-work-hub/node_modules/.bin/tsc --noEmit -p bb-plugin-work-hub/tsconfig.json
bb plugin build ./bb-plugin-work-hub
bb plugin install ./bb-plugin-work-hub
bb plugin config work-hub set hubPath "$WORK_HUB_ROOT/hub"
bb plugin config work-hub set pythonPath "$WORK_HUB_ROOT/.venv/bin/python"
bb plugin config work-hub set collectThreads false
bb plugin config work-hub set remindersEnabled false
bb plugin reload work-hub
bb work-hub status
```

Check current CLI help if your version differs. Open **Work Hub** in BB's sidebar. After accepting the BB capture scope, enable it:

```bash
bb plugin config work-hub set collectThreads true
bb plugin reload work-hub
bb work-hub sync
```

`sync` reconciles BB conversations only. Keep reminders off until you choose them and test delivery.

### Verify the bundled skills

Run `bb skill list --json` and confirm `work-hub`, `work-hub-plan`, `work-hub-resume`, `work-hub-follow-up`, and `work-hub-checkpoint`. They are imported with the plugin across BB providers. Start a new thread to discover updates; user/project skills with the same name can override them. See [the skill guide](docs/skills.md) for example prompts and operation limits.

## 6. Connect selected sources

Add each selected source to the encrypted `config` record before its collector test. OAuth success alone is not enough: verify a bounded read, correct account/scope, and a successful source status. Sign-in opens temporary loopback-only callbacks. Let the human complete consent and review grants that exceed the reads the collector performs.

### Google: Gmail, Calendar and Drive

Select a Google Cloud project you own (existing is fine). Enable the Gmail, Calendar and Drive APIs. Configure the OAuth audience/consent/test users or organization access. Create a **Desktop app** OAuth client and import its downloaded JSON using `.venv/bin/python hub/configure.py set google_client --input-file /absolute/private/download.json`. Set `google.account` first. Google tokens will be saved and refreshed in encrypted storage automatically.

Follow [Google's credential documentation](https://developers.google.com/workspace/guides/create-credentials). This uses standard APIs; Google MCP services and domain-wide delegation are not required. Check publishing/admin policy and token-lifetime behavior before relying on unattended operation.

```bash
.venv/bin/python connectors/google/access.py login
.venv/bin/python connectors/google/access.py probe
.venv/bin/python hub/collect.py gmail calendar drive
```

Run only the collector names you enabled. OAuth requests `gmail.readonly`, `calendar.readonly`, `drive.readonly`; those grants cover more data than the collector's bounded windows. The login verifies account/scopes and stores tokens atomically. Drive collection is metadata only.

### GitHub

```bash
gh auth login
gh auth status
.venv/bin/python hub/collect.py github
```

Reads authored/review-requested open PR descriptions. No GitHub App, webhook, review publication or cloud sandbox is needed. Existing credentials can grant more access than this collector uses; inspect them. Full review history is not captured.

### Linear

Use BB's plugin catalog (`bb plugin search linear`) and connection UI to install/configure Linear. Verify current plugin help and select your intended team:

```bash
bb linear status
bb linear doctor
bb linear teams
# Replace TEAM with the actual selected team key:
bb linear bind TEAM
bb linear sync
.venv/bin/python hub/collect.py linear
```

For a new read-only configuration, inspect and set `allowWrites=false`, `agentWrites=off`, `spawnMovesStatus=false`, `threadMovesStatus=false`, `prTransitions=false`, and `linkBackComment=false`. Verify keys against the installed version. Do not overwrite an existing intentional automation configuration without discussing it. Actual OAuth consent can be broader than these collector reads; the human reviews that grant.

### Slack

Use your own workspace-approved Slack app and [Slack's MCP user OAuth instructions](https://docs.slack.dev/ai/slack-mcp-server/). This is not the bot-token flow. Register callback `http://localhost:3102/slack/callback`. Configure `slack.team_id` and `slack.workspace_url`.

Prepare a private JSON object with `client_id` and `client_secret`, then import it using `.venv/bin/python hub/configure.py set slack_client --input-file /absolute/private/slack.json`. Use a secure editor or secret input mechanism; do not paste secrets into an AI conversation. Slack tokens are saved and refreshed in encrypted storage automatically.

Requested read scopes:

```text
channels:history channels:read groups:history groups:read
im:history im:read mpim:history mpim:read
search:read.public search:read.private search:read.im
search:read.mpim search:read.users users:read
```

```bash
.venv/bin/python hub/slack_auth.py
```

Open `http://localhost:3102/`, approve your intended workspace, then run `.venv/bin/python hub/collect.py slack`. App eligibility or admin approval can block this connection; leave Slack pending and finish independent setup rather than broadening permissions. Collection covers DMs, your messages/mentions, and thread replies, not every workspace message.

### Granola

```bash
.venv/bin/python hub/mcp_login.py granola
```

Open `http://localhost:3104/`, approve the connection, then run `.venv/bin/python hub/collect.py meetings`. It reads notes/summaries, not full transcripts. The OAuth grant can expose more tools than the client allows. See [Granola MCP](https://www.granola.ai/blog/granola-mcp).

### Better Stack

```bash
.venv/bin/python hub/mcp_login.py betterstack
```

Open `http://localhost:3103/` and authorize Read access. Use the adapter's permitted `applications` read to verify your application ID, set `betterstack.application_id` and `environment`, then run `.venv/bin/python hub/collect.py betterstack`. This observes patterns, not fixes. See [Better Stack MCP](https://betterstack.com/docs/getting-started/integrations/mcp/).

After each source, check successful reads and coverage in Work Hub. Verify token refresh from a fresh process. A failed or partial source must retain its checkpoint; never edit timestamps to hide a failure. Calling `collect.py` without names runs only configured sources; an empty source list performs no collection and reports a configuration error.

## 7. Test and enable background work

```bash
.venv/bin/python -m unittest discover -s hub -p 'test_*.py'
.venv/bin/python -m unittest discover -s connectors/google -p 'test_*.py'
node --experimental-strip-types --test bb-plugin-work-hub/reference-links.test.ts bb-plugin-work-hub/launch-context.test.ts bb-plugin-work-hub/context-hook.test.ts bb-plugin-work-hub/handoff-hook.test.ts
```

From a BB thread in your Work Hub project, capture your own context:

```bash
bb status --json | .venv/bin/python hub/configure.py set bb_context --input-file -
.venv/bin/python hub/install_local.py --dry-run
```

Verify the project ID and previewed commands privately. Do not publish the context record. The installer uses the Python executable you invoke it with, so use `.venv/bin/python`. Its preview omits the captured BB environment, which can contain private credentials.

```bash
.venv/bin/python hub/install_local.py
launchctl print "gui/$(id -u)/local.work-hub.analyze"
# Only when external sources are selected:
launchctl print "gui/$(id -u)/local.work-hub.collect"
bb work-hub status
```

Analysis runs every 90 seconds; selected-source collection every ten minutes. BB-only use installs no external collector. Existing named jobs are preserved rather than overwritten. PATH is captured at install; changing CLI locations may require updating the job or the analyzer's absolute executable path.

## Automatic context and completion checks

After connector qualification, enable conversation linking and optional automatic review using [the work lifecycle guide](docs/work-lifecycle.md). These features retain the existing source opt-ins and analysis allowance. Confirm the `work-hub-review-completion` skill appears alongside the five original skills.

## 8. Acceptance checks and everyday use

Before relying on Work Hub:

- The selected provider passes extraction, malicious-instruction, fulfillment, acknowledgment and proposal tests. A Claude-only installation needs no Codex executable.
- Each selected source shows the correct account/application/team, bounded coverage and a real successful read. Pending sources stay visible.
- A temporary synthetic ledger preserves context across restart, and completed follow-ups stay closed on replay.
- In actual BB, start/resume an agent, associate related conversations, focus two items, remove one focus, and verify context and sidebar links. A second model account is optional.
- Marking an attention item seen must not complete its underlying work. Shared commitments update once across associated work.
- Check a small human-reviewed extraction sample, backlog, failure/retry, token refresh, restart and sleep/wake. Record untested cases honestly.
- No external writes, unattended coding jobs, public ports, cloud workers or changes to existing automation ownership were introduced.

Morning routine: inspect source health and **Needs your attention**, review new arrivals, choose a small set of focused work, and resume the saved next action. **Follow-ups** track promises independently of whether work is focused. Complete or cancel a commitment only when appropriate.

Optionally enable `remindersEnabled` after confirming your timezone and a controlled test. Current policy is weekdays 9–18 and at most three batches/day. A delivery accepted by BB is not proof that an OS banner appeared.

## 9. Backup, stop and troubleshoot

Private state lives in `hub/.state/`. Use SQLite's backup API, or stop all writers for a coordinated backup; copying only `.db` while WAL writes are active is incomplete. [The core README](hub/README.md) describes portable exports/imports. Exports contain private text; reconnect credentials separately on another machine.

Stop only the jobs you installed:

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/local.work-hub.analyze.plist"
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/local.work-hub.collect.plist"
```

Restart with `launchctl bootstrap` and the same arguments. Disabling the plugin stops its UI/BB capture, not the independent jobs. Stop jobs before moving the source directory. Rebuild/reload the plugin after code changes; preserve private config and state during updates.

| Symptom | Check |
|---|---|
| `bb` missing | BB command-line installation and PATH. |
| Ledger unavailable | Absolute `hubPath`, `pythonPath`, directory permissions. |
| Missing BB conversations | Capture opt-in, seven-day scope, archive state, latest sync. |
| Connected source but no data | Correct account/scope, collector status, bounds and partial results. |
| Analysis pending | Explicit provider, CLI login or Gemini credential record, daily budget, retry time, private worker logs. |
| Terminal works but job fails | Captured PATH, OS keychain access, isolated Codex authentication if selected. |
| Expired connector | Renew the appropriate sign-in; retain checkpoints and never print tokens. |
| Delayed during sleep | Local jobs require an awake logged-in computer. |

The public source has local automated validation, not a completed clean-machine end-to-end installation. Account grants, organization policies and your acceptance checks determine readiness. Ask your implementing AI for a concise final report with what passed, remaining blockers, configuration locations and stop/restart commands—never credentials or source payloads.

## Native BB Automations

Enable the BB Automations and Workflows plugins. Run schedules on the BB server that owns the Work Hub database and connectors. The collector and analyzer keep their existing schedules. Install only the checks you want. Before creating a schedule, inspect `bb automation list --project PROJECT_ID --json` for one with the same name.

From this repository, substitute the actual BB project ID and local timezone. For each selected operation, set `tick` to `health-check`, `reply-check`, or `dispatch` and run:

```sh
bb automation create --project "$project_id" --name "Work Hub · $tick" \
  --cron '*/15 * * * *' --timezone "$work_timezone" \
  --script-file hub/bb-tick.sh --interpreter sh --timeout 10m \
  --env-json "{\"WORK_HUB_TICK\":\"$tick\"}"
```

- **health-check** raises a Work Hub attention item when coverage stays degraded across checks. Recovery clears the active alert.
- **reply-check** checks up to four due Slack or Gmail conversations per tick, oldest check first. An open obligation owed to you qualifies automatically. For an individual sent question, use `bb work-hub watch-reply URL SENT_MESSAGE_ID [WORK_ID]`. A detected reply appears in attention for review. Original source permissions, bounded history and the sending identity affect coverage.
- **dispatch** launches at most one private agent per tick. Configure the morning GitHub repository and/or the Better Stack application first. The first poll records existing eligible revisions and patterns as a baseline. Subsequent requests, head changes, new unresolved patterns and observed recurrences can trigger work. To pilot a current PR, use `bb work-hub dispatch PR_URL`. Reviews use separate persistent clones. BB Workflows runs the full review skill followed by an artifact and revision check. Triage opens a visible private investigation. Results live in the linked thread. External publication requires a separate decision.
- **Finish day** opens a visible close-out thread. The optional nightly reconciliation continues in the background. `bb work-hub close-out run` starts reconciliation without opening a conversation.

Pause a schedule with `bb automation pause AUTOMATION_ID --project PROJECT_ID`. BB copies script files when a schedule is created. Refresh the stored script when changing the wrapper.
