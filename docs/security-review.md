# Public-release security review

Reviewed 17 November 2026. Scope: the Work Hub Python core, Google/Slack/Granola/Better Stack connectors, analyzer adapters, BB TypeScript plugin, dependency locks, setup instructions, and the files selected for publication. The earlier private trial workspace and its history are not this repository.

## Result

No confirmed critical/high exploitable issue or embedded live credential was identified in the reviewed public source. This is a bounded review, not a penetration test or a guarantee of safety. Publication is appropriate as an experimental single-user local tool with the limits in SECURITY.md.

## Publication blockers addressed

| Finding | Impact | Public-source resolution |
|---|---|---|
| Private trial evidence lived alongside source and was not fully excluded by Git | Accidental disclosure if the entire old workspace were committed | New repository built from an explicit source-only list; excludes trial evidence, operational history, private state, credentials, logs, databases and generated files. Broad private-file ignore rules added. |
| Personal identity, machine path, workspace and application defaults were embedded in code | New installs could target the wrong account, workspace or machine | Neutral UI/fixtures; private configuration records for identity/accounts; explicit BB hub/Python paths; no copied account IDs. |
| BB capture defaulted on across projects | Unexpected capture of private conversations on install | Capture defaults off; setting and guide disclose cross-project and hidden-thread scope. |
| External collection defaulted to all connectors | More source access than the new user selected | Empty source list performs no collection. Requested collectors must be explicitly enabled. Scheduled arguments contain only selected sources. |
| Background analysis implicitly chose a provider | Private evidence could go to an unchosen provider/account | Explicit provider configuration required; no silent fallback. |
| Legacy runtime folders and experimental worker scripts were mixed with the product | Confusing setup and accidental execution of obsolete integrations | Neutral `connectors/google` and `hub/.state` paths; old bridge/review scripts and runtime removed. |

## Additional hardening

Granola XML parsing now uses `defusedxml` with DTD/entity/external references forbidden and a response-size limit. The original wrapper already prevented a top-level DTD from being accepted; no credential-reading XXE exploit was demonstrated. This is defense in depth, not a claimed confirmed exploit.

The local installer previews planned jobs, checks for existing jobs before writing, installs no collector for BB-only use, and cleans up newly written jobs if installation fails. Original running installations are not modified by these public-source changes.

## Automated checks

- **Gitleaks 8.30.1:** source-only and proposed-publication-file scans. The original scan's one detection was SQL `token=excluded.token`, with a generated UUID lease—not an embedded credential. A narrowly scoped inline allow comment documents that exact statement; other code remains scanned.
- **npm audit:** 199 dependency entries reported; zero known advisories at review time.
- **pip-audit:** 39 pinned Python packages checked; zero known advisories at review time. Dependencies were resolved/installed separately; the audit checked the pinned inventory with `--no-deps --disable-pip`.
- **Bandit:** 2,340 Python source lines scanned. Six medium SQL-construction warnings were manually reviewed: fragments are static or selected from fixed allowlists; data values use placeholders. No SQL injection path was established. Remaining low warnings concern fixed-argument subprocesses, empty pagination cursors, OAuth's `none` authentication method, and synthetic-test assertions. These were reviewed rather than hidden with a blanket suppression.
- **Python:** 76 hub tests and 10 Google adapter tests pass, including new opt-in/configuration and XML controls.
- **TypeScript:** eight link/context tests pass, typecheck passes, plugin build passes.
- **Claude:** the public copy passed four live synthetic checks: malicious instructions ignored, acknowledgment retains a promise, explicit fulfillment resolves it, and a proposal creates no commitment. No private source data was used for these checks.

## Manual review

Reviewed source-to-command boundaries (argument arrays, no source-selected shell), RPC input validation, SQL values/identifier allowlists, source-link rendering, OAuth state/PKCE and token persistence, fixed source endpoints, refresh issuer/resource checks, model tool isolation, ledger leases/version checks, privacy defaults, and publication contents. React renders source strings as text; no source-fed raw HTML/eval path was found in the plugin.

## Residual limits

- Local settings, CLI binaries/PATH, the OS account, BB and provider-managed policy are trusted. This does not isolate a malicious process running as the same user.
- The SQLite ledger is plaintext local data protected by filesystem permissions and OS security. Configuration and Work Hub-owned OAuth records now default to BB Env Catalog encryption; optional JSON overrides remain plaintext. Provider-owned CLI sign-ins retain their provider storage. Backups/exports need equivalent care. See storage.md for the follow-up change and its validation.
- Claude/Codex receive selected source excerpts. Tool restrictions do not guarantee that summaries or obligation extraction are correct.
- OAuth grants can be broader than the client read allowlist. Organization-specific consent and refresh must be tested on each installation.
- Explicit BB capture spans projects, including hidden threads. No per-project filter exists yet.
- Link opening is user initiated through BB; review a source's destination before following an unfamiliar link.
- No fresh-machine end-to-end installation, multi-user deployment, malicious-provider test, independent penetration test, or live autonomous coding-worker qualification was performed.
- Runtime providers and dependency advisories change. Rerun checks after updates; retain isolation controls when CLI flags change.

## Reproduce the scans

Run from a clean checkout, using installed scanners:

```bash
gitleaks git --redact --no-banner
bandit -r hub connectors/google -x hub/.state,hub/__pycache__,connectors/google/__pycache__
npm audit --prefix bb-plugin-work-hub
pip-audit -r connectors/google/requirements.txt --no-deps --disable-pip
```

Do not upload private `.state`, logs, exports or raw scan matches to public issues. Reports can contain file paths or snippets; review/redact before sharing.

## Encrypted storage follow-up

The configuration/credential update adds BB Env Catalog (0.3.3) as the default, explicit environment/JSON overrides, read-back migration verification, and refresh writeback. 93 hub tests and 12 Google adapter tests pass, alongside eight TypeScript tests, typecheck and plugin build. A source-only Gitleaks scan found no secrets. The installed application passed bounded Google/Slack account reads, authenticated Better Stack/Granola tool discovery, a Work Hub snapshot, and analyzer restart. These live checks do not establish fresh-machine setup or cryptographic correctness of the catalog plugin.
