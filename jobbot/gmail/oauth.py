"""
Google OAuth2 flow for Gmail readonly access.
Tokens stored in data/gmail_token.json.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
_LAST_SYNC: Optional[str] = None


def _load_creds():
    """Load and auto-refresh stored credentials, or return None if not present."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        raise RuntimeError("google-auth not installed — run: pip install google-auth google-auth-oauthlib google-api-python-client")

    token_path = config.GMAIL_TOKEN_PATH
    if not token_path.exists():
        return None

    creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_creds(creds)
    return creds if creds and creds.valid else None


def _save_creds(creds) -> None:
    config.GMAIL_TOKEN_PATH.write_text(creds.to_json())


def is_connected() -> bool:
    try:
        return _load_creds() is not None
    except Exception:
        return False


def get_last_sync() -> Optional[str]:
    return _LAST_SYNC


def start_oauth_flow(callback_url: str) -> str:
    """Return the Google authorization URL to redirect the user to."""
    from google_auth_oauthlib.flow import Flow  # type: ignore

    if not config.GMAIL_CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            f"Gmail credentials file not found at {config.GMAIL_CREDENTIALS_PATH}. "
            "Download it from Google Cloud Console (OAuth 2.0 client secret)."
        )

    flow = Flow.from_client_secrets_file(
        str(config.GMAIL_CREDENTIALS_PATH),
        scopes=_SCOPES,
        redirect_uri=callback_url,
    )
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    # Store the flow state in a temp file so finish_oauth_flow can use it
    state_path = config.DATA_DIR / "gmail_oauth_state.json"
    state_path.write_text(json.dumps({"redirect_uri": callback_url}))
    return auth_url


def finish_oauth_flow(code: str, callback_url: str) -> None:
    """Exchange the auth code for tokens and persist them."""
    from google_auth_oauthlib.flow import Flow  # type: ignore

    flow = Flow.from_client_secrets_file(
        str(config.GMAIL_CREDENTIALS_PATH),
        scopes=_SCOPES,
        redirect_uri=callback_url,
    )
    flow.fetch_token(code=code)
    _save_creds(flow.credentials)


def get_gmail_service():
    """Return an authorized Gmail API service object."""
    from googleapiclient.discovery import build  # type: ignore

    creds = _load_creds()
    if not creds:
        raise RuntimeError("Gmail not connected. Visit /gmail/auth to authorise.")
    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    global _LAST_SYNC
    _LAST_SYNC = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return service
