"""Local OAuth and bounded read-only Google source qualification."""

import argparse
import json
import os
import sys

from datetime import datetime, timezone
from pathlib import Path

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT / "hub"))
from settings import google_account
from storage import Store
STATE = ROOT / "hub" / ".state" / "google"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]



def save_credentials(credentials):
    Store(STATE.parent).write('google_tokens',json.loads(credentials.to_json()))


def load_credentials():
    return Credentials.from_authorized_user_info(Store(STATE.parent).read('google_tokens') or {}, scopes=SCOPES)


def read_json(session, url, params=None):
    response = session.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def probe(credentials):
    if not credentials.refresh_token:
        raise ValueError("Offline access was not granted")
    Store(STATE.parent).require_writable('google_tokens')
    credentials.refresh(Request())
    with AuthorizedSession(credentials, max_refresh_attempts=0) as session:
        profile = read_json(session, "https://gmail.googleapis.com/gmail/v1/users/me/profile")
        if profile["emailAddress"].lower() != google_account():
            raise ValueError("Unexpected Google account")
        checks = {}
        for name, url, params, key in [
            ("gmail", "https://gmail.googleapis.com/gmail/v1/users/me/messages",
             {"maxResults": 1, "fields": "messages(id),nextPageToken"}, "messages"),
            ("calendar", "https://www.googleapis.com/calendar/v3/users/me/calendarList",
             {"maxResults": 1, "fields": "items(id),nextPageToken"}, "items"),
            ("drive", "https://www.googleapis.com/drive/v3/files",
             {"pageSize": 1, "fields": "files(id),nextPageToken", "q": "trashed = false"}, "files"),
        ]:
            result = read_json(session, url, params)
            checks[name] = {"read_succeeded": True, "records_returned": len(result.get(key, []))}
    save_credentials(credentials)
    evidence = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "account_verified": True,
        "refresh_succeeded": True,
        "requested_scopes": SCOPES,
        "checks": checks,
        "limits": "Bounded direct API reads only; no recurring intake configured.",
    }
    (STATE / "read-result.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["login", "probe"])
    action = parser.parse_args().action
    STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
    Store(STATE.parent).require_writable('google_tokens')
    if action == "login":
        if Store(STATE.parent).read('google_tokens') is not None:
            raise FileExistsError("Existing credentials preserved; use probe")
        flow = InstalledAppFlow.from_client_config(
            Store(STATE.parent).read('google_client') or {}, scopes=SCOPES,
            autogenerate_code_verifier=True,
        )
        print("Opening Google consent for read-only Gmail, Calendar, and Drive access.", flush=True)
        credentials = flow.run_local_server(
            host="127.0.0.1", bind_addr="127.0.0.1", port=0,
            authorization_prompt_message="",
            success_message="Google sign-in received. You may close this tab; connection checks are running in BB.",
            timeout_seconds=600, login_hint=google_account(), prompt="consent",
            access_type="offline", include_granted_scopes="false",
        )
        granted = credentials.granted_scopes or flow.oauth2session.token.get("scope")
        if isinstance(granted, str):
            granted = granted.split()
        if granted is None or set(granted) != set(SCOPES):
            raise ValueError("Granted scopes did not match the three requested read-only scopes")
        # Check the account before persisting credentials, even if later probes fail.
        with AuthorizedSession(credentials, max_refresh_attempts=0) as session:
            profile = read_json(session, "https://gmail.googleapis.com/gmail/v1/users/me/profile")
            if profile["emailAddress"].lower() != google_account():
                raise ValueError("Unexpected Google account")
        save_credentials(credentials)
    else:
        credentials = load_credentials()
    probe(credentials)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # OAuth and HTTP exceptions can embed callback codes or response payloads.
        print(f"Google access check stopped ({type(error).__name__}); credentials and response bodies omitted.", file=sys.stderr)
        sys.exit(1)
