# Google source adapter

Uses standard Gmail, Calendar and Drive APIs with the user's Desktop-app OAuth client. Follow the Google section in [SETUP.md](../../SETUP.md).

Private files live under `hub/.state/google/`: `client_secret.json`, `authorized_user.json`, and counts-only probe output. Configure the expected account in `hub/.state/config.json` before sign-in. OAuth verifies identity and the requested read scopes; tokens are atomically replaced with owner-only permissions. Collection uses fixed Google endpoints and does not follow redirects.

`access.py login` performs human sign-in; `access.py probe` tests refresh and bounded reads. `server.py` provides optional read-only MCP tools over stdio, with no listening port. Source text remains untrusted evidence. Do not expose this adapter to unauthenticated clients or publish its state directory.
