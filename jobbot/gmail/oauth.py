"""
Google OAuth2 for Gmail readonly access — supports two accounts.
Tokens stored in data/gmail_token_personal.json and data/gmail_token_berkeley.json.

Run setup_gmail_oauth.py once to authorise both accounts.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

_LAST_SYNC: Optional[str] = None

_ACCOUNTS = {
    "personal":  config.GMAIL_TOKEN_PERSONAL,   # rluna727@gmail.com
    "berkeley":  config.GMAIL_TOKEN_BERKELEY,    # rlunaorozco@berkeley.edu
}


def _load_creds(token_path: Path):
    """Load and auto-refresh stored credentials, or return None."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        raise RuntimeError("Run: pip install google-auth google-auth-oauthlib google-api-python-client")

    if not token_path.exists():
        return None

    creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json())
    return creds if creds and creds.valid else None


def is_connected(account: str = "personal") -> bool:
    try:
        return _load_creds(_ACCOUNTS[account]) is not None
    except Exception:
        return False


def connected_accounts() -> list[str]:
    """Return list of account keys that have valid tokens."""
    return [name for name in _ACCOUNTS if is_connected(name)]


def get_last_sync() -> Optional[str]:
    return _LAST_SYNC


def get_gmail_service(account: str = "personal"):
    """Return an authorized Gmail API service for the given account."""
    from googleapiclient.discovery import build  # type: ignore

    token_path = _ACCOUNTS.get(account)
    if not token_path:
        raise ValueError(f"Unknown account '{account}'. Choose from: {list(_ACCOUNTS)}")

    creds = _load_creds(token_path)
    if not creds:
        raise RuntimeError(
            f"Gmail not connected for '{account}'. "
            "Run: python setup_gmail_oauth.py"
        )

    global _LAST_SYNC
    _LAST_SYNC = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def get_all_gmail_services() -> dict:
    """Return {account_name: service} for every connected account."""
    services = {}
    for name in _ACCOUNTS:
        try:
            services[name] = get_gmail_service(name)
        except Exception:
            pass
    return services


# ── Legacy Flask OAuth (kept for reference, not used) ─────────────────────────
# The installed-app flow requires setup_gmail_oauth.py, not a web callback.

def start_oauth_flow(callback_url: str) -> str:
    raise NotImplementedError(
        "Flask OAuth callback not supported for installed-app credentials. "
        "Run setup_gmail_oauth.py to authorise Gmail access."
    )


def finish_oauth_flow(code: str, callback_url: str) -> None:
    raise NotImplementedError(
        "Flask OAuth callback not supported. Run setup_gmail_oauth.py."
    )
