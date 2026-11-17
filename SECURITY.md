# Security and privacy

Work Hub is an experimental **single-user local application**. It is not a multi-user service, a security boundary against software running as the same OS user, or an internet-facing API. Keep BB and the laptop protected; use OS disk encryption and normal account security.

## Data handling

The SQLite ledger contains private excerpts, commitments, and links. Tokens and credentials are stored separately under ignored `hub/.state/` with owner-only files. They are not encrypted by the application. Backups and ledger exports also contain private data; protect them and never attach them to public issues.

Collection is off until configured. BB capture is a separate opt-in and currently spans recent unarchived threads across projects, including hidden threads. There is no per-project filter. Selected source excerpts are sent to the explicitly configured Claude or Codex service. Background analysis shares the selected account's usage/billing. No provider fallback is performed.

Source adapters restrict operations to reads. OAuth consent may grant broader server capabilities, especially Granola; the client's read allowlist is an additional application control, not a narrower OAuth grant. Google grants cover more data than the collector's bounded date window.

## Trust boundaries

- Source text is untrusted data. Extraction runs without general tools, source MCP access, personal instructions or skills, then passes evidence/version checks before ledger changes.
- These controls reduce prompt-injection risk; they do not prove extraction is correct. Review ambiguous commitments and check source coverage.
- Starting an agent opens an editable BB composer. The chosen agent can have much broader permissions than the analyzer; its BB permissions and the user's task control execution.
- Plugin settings, local configuration, installed CLI binaries, provider-managed policy and the OS account are trusted. Protect them from unauthorized edits.
- OAuth helpers bind to loopback temporarily. No router forwarding, public webhook receiver, cloud host or public laptop port is needed.
- Live PR review and error-fixer triggers are not shipped/enabled in this public version.

## Reporting a vulnerability

Use this repository's private vulnerability reporting feature if enabled. Do not publish working credentials, private source text, or exploit details in a public issue. If private reporting is unavailable, open a minimal issue requesting a private contact without sensitive details.

## Validation limits

The release audit combines manual review, Gitleaks, Bandit, npm audit, pip-audit, and synthetic tests. It is not a penetration test or a guarantee of security. Dependency advisories and provider behavior change; rerun checks after updates. See [the audit report](docs/security-review.md) for measured scope and residual risks.
