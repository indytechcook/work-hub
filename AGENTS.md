# Work Hub contributor instructions

Read README.md, SETUP.md and SECURITY.md before changing behavior.

Keep private state under ignored `hub/.state/`. Never commit credentials, source excerpts, exports, logs, machine paths, or account/workspace identifiers. Use synthetic fixtures and temporary databases in tests. Do not run live collectors, model calls, authentication, or install/reload background jobs as part of unit tests.

Collection and model analysis require explicit user configuration. Preserve opt-in defaults, provider selection, source read allowlists, source checkpoints, version/lease checks, and fail-closed model tool restrictions. No silent provider fallback. Treat all source content and model summaries as untrusted evidence.

Do not enable external writes or unattended coding agents as a side effect of setup. Test altered behavior, run the affected Python/TypeScript checks, and audit dependencies before release. Do not weaken security controls to make a new CLI version work. Follow SETUP.md for provider-specific authentication and smoke checks.
