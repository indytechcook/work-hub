# Install Work Hub and Chief of Staff

This guide takes you from a fresh BB install to a working **Work Hub** and **Chief of Staff** panel on your own Mac, using your own accounts. Plan on about an hour. Most of that is signing in to things.

- **Work Hub** collects your work from GitHub, Linear and (optionally) Slack, Gmail, Calendar, Drive, Granola and Better Stack. It keeps a private local register of work items and commitments, and runs the morning plan.
- **Chief of Staff** takes the work you hand it, opens one supervised agent thread per item, answers their routine questions, and brings you only the decisions you need to make.

Everything runs on your laptop under your own sign-ins. Nothing is shared with teammates. Selected source excerpts go to the model you choose for background analysis (Claude, Codex or Gemini). Read [SECURITY.md](SECURITY.md) before you connect a private source like Slack or Gmail.

> **Start small.** Connect GitHub and Linear first. Run Chief of Staff with one or two agents at a time and PR writes off for the first week. Add the rest once you trust what it does.

Both doctors are read-only. Run them after any step to see what is still missing:

```bash
.venv/bin/python hub/doctor.py   # before the plugin is installed, from the work-hub folder
bb work-hub doctor               # once the Work Hub plugin is installed
bb cos doctor                    # once Chief of Staff is installed
```

## 0. Prerequisites

| Need | Version and check | Notes |
|---|---|---|
| macOS | Signed in and awake | Background jobs use `launchd`. Linux and Windows are not set up yet. |
| BB | 0.50 or newer (`bb status` works) | Download from [getbb.app](https://getbb.app/). Install the CLI under **Settings → General → Command line → Install**. |
| Node.js and npm | Node 22 or newer (tested on 26.13) | `node --version` |
| Python | 3.12 (`python3.12 --version`) | `brew install python@3.12` |
| GitHub CLI | `gh auth status` shows you signed in | Needs read access to the two private repos below and to the repos you work in. |
| A model CLI | `claude auth status` (Claude Code) or `codex login status` | One is enough. Used for background analysis. Your BB threads can use any provider. |
| Linear | Your normal Linear account | Connected through BB's Linear plugin in step 5. |
| Access | Read access to `Liftify-Dev/work-hub` and `Liftify-Dev/bb-plugin-chief-of-staff` | Ask whoever shared this guide. |

Optional, only for the sources you want later: a Google Cloud project you own (Gmail, Calendar, Drive), a Slack app your workspace allows, Granola, and Better Stack.

## 1. Get the code

Pick a stable folder. The plugins and the background jobs point at absolute paths inside it, so don't move it later.

```bash
mkdir -p ~/src && cd ~/src
gh repo clone Liftify-Dev/work-hub
gh repo clone Liftify-Dev/bb-plugin-chief-of-staff
export WORK_HUB_ROOT="$HOME/src/work-hub"
export COS_ROOT="$HOME/src/bb-plugin-chief-of-staff"
```

## 2. Set up the Work Hub core

```bash
cd "$WORK_HUB_ROOT"
umask 077
mkdir -p hub/.state/google
chmod 700 hub/.state hub/.state/google
python3.12 -m venv .venv
.venv/bin/python -m pip install -r connectors/google/requirements.txt
npm ci --prefix bb-plugin-work-hub
```

`hub/.state/` holds your private register and is ignored by git. Never copy someone else's `.state`, `.venv` or tokens into your clone.

## 3. Prepare BB

```bash
bb plugin install env-catalog@bb-community --yes   # encrypted storage for Work Hub settings and tokens
bb plugin list | grep -E '^(automations|workflows|push-notifications)@'   # all three should be running
bb project create --name "Work Hub" --root "$WORK_HUB_ROOT" --json
```

If one of the three built-in plugins shows `disabled`, run `bb plugin enable <name>`. Then open a thread in the new **Work Hub** project and send a short test message, so you know your model works in BB.

## 4. Configure Work Hub (no sources yet)

Write your identity to a private file outside the repo, then import it. Keep `"sources": []` for now: that means no external collection.

```bash
cp examples/config.json ~/work-hub-config.json
# Edit ~/work-hub-config.json: your name, work email and IANA timezone (e.g. America/New_York).
.venv/bin/python hub/configure.py set config --input-file ~/work-hub-config.json

# Background analyzer: pick ONE.
.venv/bin/python hub/configure.py set analyzer --input-file examples/analyzer.claude.json
# .venv/bin/python hub/configure.py set analyzer --input-file examples/analyzer.codex.json

# Daily cap on background model calls.
echo '{"max_provider_used_percent":90}' | .venv/bin/python hub/configure.py set policy --input-file -

# Check the analyzer with synthetic data (a few live model calls).
.venv/bin/python hub/analyze.py --self-test --provider claude
.venv/bin/python hub/doctor.py
```

Use `--provider codex` if you chose Codex. Codex needs an isolated sign-in; see [SETUP.md §4](SETUP.md#4-select-the-background-analyzer). For Gemini, follow the Gemini section there too. After the import works, you can delete `~/work-hub-config.json`.

## 5. Install the Work Hub plugin

```bash
cd "$WORK_HUB_ROOT"
./bb-plugin-work-hub/node_modules/.bin/tsc --noEmit -p bb-plugin-work-hub/tsconfig.json
bb plugin build ./bb-plugin-work-hub
bb plugin install ./bb-plugin-work-hub
bb plugin config work-hub set hubPath "$WORK_HUB_ROOT/hub"
bb plugin config work-hub set pythonPath "$WORK_HUB_ROOT/.venv/bin/python"
bb plugin reload work-hub
bb work-hub doctor
```

Open **Work Hub** in BB's sidebar. It is empty until you connect a source or turn on BB capture (step 9).

## 6. Install the Chief of Staff plugin

```bash
cd "$COS_ROOT"
npm ci
npx tsc --noEmit -p .
npm test
bb plugin build .
bb plugin install "$COS_ROOT"
bb plugin config chief-of-staff set maxConcurrent 1
bb plugin config chief-of-staff set allowPrWrites false
bb cos doctor
```

Open **Chief of Staff** in the sidebar. Then pick a default project for agent threads in **Settings → Plugins → Chief of Staff → Project for new agent threads**. Use the project for the repo you work in most; items that name no project start there.

## 7. Connect the first sources: GitHub and Linear

**GitHub** uses your `gh` sign-in. It reads open PRs you authored or were asked to review.

```bash
gh auth status
```

**Linear** uses BB's Linear plugin. Install it from **Settings → Plugins** (search "Linear") or with `bb plugin install linear`, connect your account in its panel, then:

```bash
bb linear status
bb linear doctor
bb linear teams
bb linear bind TEAM      # your team's key, from the list above
bb linear sync
```

For a read-only Linear setup, check the plugin's write settings. Only change them if you didn't set them on purpose: `allowWrites=false`, `agentWrites=off`, `spawnMovesStatus=false`, `threadMovesStatus=false`, `prTransitions=false`, `linkBackComment=false`.

Now turn both sources on and collect once:

```bash
cd "$WORK_HUB_ROOT"
# Edit ~/work-hub-config.json (or a fresh copy of examples/config.json): "sources": ["github", "linear"]
.venv/bin/python hub/configure.py set config --input-file ~/work-hub-config.json
.venv/bin/python hub/collect.py github linear
bb work-hub doctor
```

The **Coverage** lines in the doctor output should show a recent good read for both.

## 8. Turn on background jobs

The jobs collect every 10 minutes and analyze every 90 seconds. Run the first command **from a BB thread in your Work Hub project**, so it captures the right project:

```bash
cd "$WORK_HUB_ROOT"
bb status --json | .venv/bin/python hub/configure.py set bb_context --input-file -
.venv/bin/python hub/install_local.py --dry-run    # check: your project, only github + linear
.venv/bin/python hub/install_local.py
bb work-hub doctor                                  # Job: analyze and Job: collect should be loaded
```

The installer refuses to overwrite existing jobs. If you change your source list later, remove the jobs (step 12) and install them again.

## 9. Optional: more sources and BB capture

Add one at a time. After each, re-import your config with the new source in `sources`, run `.venv/bin/python hub/collect.py <source>`, and check `bb work-hub doctor`.

| Source | Config `sources` name | How to connect |
|---|---|---|
| Gmail, Calendar, Drive | `gmail`, `calendar`, `drive` | Your own Google Cloud **Desktop app** OAuth client. Set `google.account` first. [SETUP.md §6 Google](SETUP.md#google-gmail-calendar-and-drive) |
| Slack | `slack` | A Slack app your workspace allows, user OAuth. Set `slack.team_id` and `slack.workspace_url`. [SETUP.md §6 Slack](SETUP.md#slack) |
| Granola | `meetings` | `.venv/bin/python hub/mcp_login.py granola` [SETUP.md §6 Granola](SETUP.md#granola) |
| Better Stack | `betterstack` | `.venv/bin/python hub/mcp_login.py betterstack` [SETUP.md §6 Better Stack](SETUP.md#better-stack) |

**BB capture** links your BB conversations to work. Limit it to the projects you want captured, and leave hidden agent threads out:

```bash
bb project list                                         # find your project ids
bb plugin config work-hub set threadProjects proj_aaa,proj_bbb
bb plugin config work-hub set includeHiddenThreads false
bb plugin config work-hub set collectThreads true
bb plugin reload work-hub
bb work-hub sync
```

Optional BB automations (health check, reply watches, agent dispatch for review requests) are in [SETUP.md → Native BB Automations](SETUP.md#native-bb-automations). Skip them for the first week.

## 10. Review every setting

Change settings with `bb plugin config <plugin> set <key> <value>`, or in **Settings → Plugins**. Work Hub reads its settings at startup, so run `bb plugin reload work-hub` after changing one.

### Chief of Staff

| Setting | Start with | What it does |
|---|---|---|
| `maxConcurrent` | **1–2** | Agents working at once. Raise it once you keep up with Needs you. |
| `allowPrWrites` | **off** | On lets the panel merge your PRs, auto-merge when you mark a draft ready, and agents push fixes to PR branches. Off keeps merges and pushes with you. |
| `defaultProject` | Your main repo's project | Where an item that names no project starts its thread. |
| `workerPermissionMode` | `default` | Permission mode for agent threads. `auto` and `full` ask you less. |
| `maxAutoReplies` | 6 (or 2–3 to start) | Answers and nudges per item before it must come to you. |
| `guidance` | A few lines | What the chief of staff may decide for you, and what to always bring to you. |
| `deployWorkflow` | Your production deploy workflow file, e.g. `deploy.yml` | Lets post-deploy steps appear once production has the merge. Blank shows them as "deploy not confirmed". |
| `postDeployDays` | 7 | How far back to look for merged PRs with post-deploy steps. |
| `rulesFile` | Blank | Optional absolute path to your own rules file. The standing-order builder reads it, read-only. |
| `muteAgentThreads` | on | Silences notifications from threads the chief of staff runs. You hear about them through Needs you instead. |
| `notifyNeedsYou` | on | One notification when something lands in Needs you. |
| `useWorktrees` | on | Each agent in a git project gets its own worktree, never your main checkout. |
| `archiveFinishedAfterHours` | 24 | Archives finished agent threads once idle. |
| `approvalsPaused` | off, with no orders on | Pauses every standing order at once. A fresh install has no standing orders. |
| `workHubMarkDone` | on | Marks the Work Hub item done when its agent finishes. |

### Work Hub

| Setting | Start with | What it does |
|---|---|---|
| `hubPath`, `pythonPath` | Absolute paths from step 5 | Where the core and its venv live. |
| `collectThreads` | off, then on | Captures recent BB conversations. |
| `threadProjects` | Your work project ids | Projects to capture from. Blank means **every** project, personal ones included. |
| `includeHiddenThreads` | **off** | Hidden threads are internal agent threads. |
| `autoContext` | off | Links new conversations and fetches referenced context automatically. Turn it on after a week. |
| `remindersEnabled` | off | Grouped follow-up reminders on weekdays. |
| `nightlyCloseOut` | off | Deep reconciliation at 10 pm. |

## 11. Check that it works

1. `bb work-hub doctor` and `bb cos doctor` report no problems.
2. `bb work-hub status` returns JSON with your work, and Work Hub's sidebar lists items from GitHub and Linear.
3. In Work Hub, choose **Start morning**, answer its questions and save a plan.
4. In Chief of Staff, choose **Hand off work** and delegate one small, safe item: a doc fix, or a question to investigate. Or from a terminal: `bb cos add --local "Read the README of <repo> and list three unclear setup steps"`.
5. Watch the item move to **working**. Answer anything that lands in **Needs you**. `bb cos brief` prints the same briefing.
6. The agent stops at a draft PR or a report. Nothing merges, deploys or sends a message without you.

## 12. Turn it off or disconnect everything

Pause first, then remove as much as you want, in this order.

```bash
# Stop starting agents, and stop every standing order.
bb cos pause
bb plugin config chief-of-staff set approvalsPaused true

# Stop background collection and analysis.
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/local.work-hub.analyze.plist"
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/local.work-hub.collect.plist"
rm -f "$HOME/Library/LaunchAgents/local.work-hub.analyze.plist" "$HOME/Library/LaunchAgents/local.work-hub.collect.plist"

# Remove any Work Hub automations you created (step 9).
bb automation list --project <project-id> --json      # then: bb automation delete ... for the "Work Hub ·" ones

# Unload the plugins. Uninstall also deletes their settings and schedules; your clones stay on disk.
bb plugin disable chief-of-staff && bb plugin disable work-hub
bb plugin uninstall chief-of-staff && bb plugin uninstall work-hub

# Delete stored Work Hub settings and tokens (names start with WORK_HUB_).
bb env-catalog list --query WORK_HUB_                  # then: bb env-catalog delete <NAME> for each

# Delete the private register.
rm -rf "$WORK_HUB_ROOT/hub/.state"
```

Then revoke the grants at each provider: Google account → **Security → Third-party access**, Slack → **Apps → your app → Remove**, Granola and Better Stack connected apps, Linear → **Settings → Connected accounts** (or uninstall BB's Linear plugin), and `gh auth logout` if you no longer need GitHub.

## Updating

```bash
cd "$WORK_HUB_ROOT" && git pull && npm ci --prefix bb-plugin-work-hub && .venv/bin/python -m pip install -r connectors/google/requirements.txt
bb plugin build ./bb-plugin-work-hub && bb plugin reload work-hub
cd "$COS_ROOT" && git pull && npm ci && bb plugin build . && bb plugin reload chief-of-staff
bb work-hub doctor && bb cos doctor
```

## Troubleshooting

| Symptom | Check |
|---|---|
| `bb` not found | Install BB's command line (prerequisites). |
| Work Hub panel says the ledger is unavailable | `bb work-hub doctor`: `hubPath` and `pythonPath` must be absolute. |
| Doctor says "Storage … unavailable" | `bb plugin list` shows `env-catalog` running. |
| Analyzer fails in the background but works in a terminal | The job captured an old PATH. Remove and reinstall the jobs (steps 12 and 8). |
| Chief of Staff says Work Hub isn't running | `bb plugin list`, then `bb work-hub doctor`. |
| No draft-PR holds or reviewer picks | `gh auth status` for the user BB runs as. |
| A source shows no data | `bb work-hub doctor` coverage lines, then `.venv/bin/python hub/collect.py <source>`. |

More detail: [SETUP.md](SETUP.md) (every source and option), [SECURITY.md](SECURITY.md), [docs/morning.md](docs/morning.md), and the Chief of Staff [README](https://github.com/Liftify-Dev/bb-plugin-chief-of-staff#readme).
