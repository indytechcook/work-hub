# Configuration and credential storage

Work Hub uses **BB Env Catalog** by default. This is the BB community plugin with AES-256-GCM encrypted values, not ordinary BB plugin settings (whose `secret` fields are protected plaintext). Tested with Env Catalog 0.3.3 and BB 0.49.0. Install it in the same BB server used by Work Hub:

```sh
bb plugin install env-catalog@bb-community --yes
.venv/bin/python hub/configure.py status
```

`status` shows record names, presence and selected origins, never values. Env Catalog uses a local encryption key, is available to enrolled machines/agents, and is not an OS-user isolation boundary. Protect the entire BB state and key together. See [plugin source and documentation](https://github.com/VKirill/bb-plugin-env-catalog).

## Precedence

Each record is a complete JSON object. Resolution is:

1. `WORK_HUB_<RECORD>_JSON` environment variable, if present.
2. That record in `WORK_HUB_CONFIG_FILE`, an explicitly selected absolute JSON path.
3. BB encrypted storage.

Objects are **replaced, not merged**. Missing JSON override files in BB mode, malformed objects and empty explicit environment overrides fail; they never fall back silently. Missing BB/plugin access fails visibly. Record names:

| Record | Contents |
|---|---|
| `config` | Identity, selected sources, account/workspace/application settings; see examples/config.json. |
| `analyzer` | Explicit `provider`, optional `model` and `executable`. |
| `policy` | Analysis allowance and optional catch-up policy. |
| `bb_context` | Output of `bb status --json` for scheduler setup. |
| `google_client` | Downloaded Google Desktop OAuth client JSON. |
| `google_tokens` | Google authorized-user credentials, managed by the sign-in helper. |
| `slack_client` | `client_id` and `client_secret`. |
| `slack_tokens` | Slack user OAuth result, managed by the sign-in helper. |
| `betterstack_client`, `granola_client` | MCP OAuth client registration, managed by the sign-in helper. |
| `betterstack_tokens`, `granola_tokens` | MCP OAuth grant with persisted `_expires_at`, managed by the sign-in helper. |

Example: `WORK_HUB_ANALYZER_JSON='{"provider":"claude"}'` overrides only the analyzer. Do not put credential values into shell history or an AI transcript; use your environment manager or private files.

`WORK_HUB_PROFILE` defaults to `default`; lowercase letters, digits and underscores are allowed, starting with a letter. Catalog names look like `WORK_HUB_DEFAULT_GOOGLE_TOKENS`. Use a distinct profile per independent installation/account. Run only one installation/host against a given OAuth profile: provider refresh tokens may rotate, and local refresh locks do not coordinate across hosts.

## Explicit JSON storage

A bundle contains record names at the top level:

```json
{
  "config": {"sources": []},
  "analyzer": {"provider": "claude"},
  "policy": {"max_analyses_per_day": 120}
}
```

For standalone storage without BB catalog access:

```sh
export WORK_HUB_STORAGE=json
export WORK_HUB_CONFIG_FILE=/absolute/private/work-hub.json
```

In `json` mode, absent records stay absent and BB is never queried. Without `WORK_HUB_CONFIG_FILE`, this explicit mode uses `hub/.state/settings.json`. With default `bb` mode, records absent from the override file still use BB. New records are written to BB unless `json` mode is selected; an existing JSON override is updated in place. File writes preserve other records, lock writers, use atomic replacement and mode 0600. JSON files are **not encrypted**. Keep them outside source control and protect their backups.

Environment record overrides are read-only. OAuth helpers refuse renewal before consuming a refresh token supplied through an environment override. Use BB or JSON for automatically refreshed grants; environment overrides work well for static client credentials and configuration. An expired environment token requires replacing that environment value yourself. Google reads disable hidden automatic renewal so credentials cannot rotate outside the persisted refresh path.

## Set records and migrate

```sh
.venv/bin/python hub/configure.py set analyzer --input-file examples/analyzer.claude.json
bb status --json | .venv/bin/python hub/configure.py set bb_context --input-file -
.venv/bin/python hub/configure.py set google_client --input-file /absolute/private/download.json
```

Input travels through stdin to BB, never secret command arguments. `set` reads back and verifies without printing values. Direct editing in Env Catalog is possible; retain the JSON object format.

For an existing installation, stop its collector/analyzer and any sign-in helpers first. Preserve the ledger. Preview then copy the legacy `hub/.state/` records:

```sh
.venv/bin/python hub/configure.py migrate --legacy-root /absolute/work-hub
.venv/bin/python hub/configure.py migrate --legacy-root /absolute/work-hub --apply
.venv/bin/python hub/configure.py status
```

Migration refuses differing destination records, can resume an interrupted copy, verifies each record, preserves old MCP token expiry, and leaves originals intact. It recognizes the former config/analyzer/policy/bb-context files, Google client and authorized-user files, Slack client `.env` and token file, and MCP client/token files. Historical installations with other layouts need an explicit import. Do not overwrite a newer refreshed token with an old backup. After verification and restart, remove only obsolete Work Hub-owned plaintext credential files; preserve grants/files still owned by another application. Legacy files are never an automatic runtime fallback.

The macOS installer captures the storage backend/path/profile for scheduled jobs. It refuses process-only `*_JSON` overrides instead of serializing secrets into plists or silently losing those overrides. Existing jobs require an explicit environment update if you change storage routing. The BB Work Hub plugin exposes nonsecret `storageBackend`, `storageProfile`, and `storageFile` bootstrap settings; blank values inherit the plugin process environment/default. Explicit environment routing takes precedence over these settings. Set the same backend/profile/path in the plugin and scheduler, then reload the plugin. Never paste a JSON credential value into these path/profile settings. Restart processes after changing environment values. BB must be running for encrypted-store reads/refreshes; source checkpoints are not advanced on access failure.

## Boundaries

This store owns Work Hub configuration and its Google, Slack, Better Stack and Granola OAuth credentials. GitHub CLI, BB Linear authentication, Claude/Codex subscriptions/keychains and BB server authentication remain owned by those tools. Work Hub neither copies nor overrides their private databases. Plugin bootstrap paths and capture/reminder switches remain normal BB settings; they must never contain secrets. The SQLite ledger, source excerpts, diagnostic metadata and lock files remain local and unencrypted.

Tests cover precedence, no plaintext fallback, read-only overrides before refresh, JSON writeback/permissions, process-output redaction, migration expiry, and OAuth rotation/restart behavior. These are application checks, not an independent cryptographic audit of Env Catalog.
