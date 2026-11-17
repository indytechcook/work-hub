# Google source adapter

Uses standard Gmail, Calendar and Drive APIs with the user's Desktop-app OAuth client. Follow the Google section in [SETUP.md](../../SETUP.md).

Client and token records (`google_client`, `google_tokens`) use the [shared encrypted storage interface](../../docs/storage.md). Only locks and counts-only probe output remain under `hub/.state/google/`. Configure the expected account in the `config` record before sign-in. OAuth verifies identity and the requested read scopes; token refresh is persisted to the originating writable store. Collection uses fixed Google endpoints and does not follow redirects.

`access.py login` performs human sign-in; `access.py probe` tests refresh and bounded reads. `server.py` provides optional read-only MCP tools over stdio, with no listening port. Source text remains untrusted evidence. Do not expose this adapter to unauthenticated clients or publish its state directory.
