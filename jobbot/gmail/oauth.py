"""
Google OAuth2 for Gmail readonly access — supports two accounts.
Tokens stored in data/gmail_token_personal.json and data/gmail_token_berkeley.json.

Run setup_gmail_oauth.py (repo root) once on the laptop to authorise both
accounts; copy the minted token files to the VM's data dir.
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

# Cached Credentials per account — avoids re-reading the token file and
# re-hitting Google's token endpoint on every is_connected()/service call.
_creds_cache: dict = {}


def _migrate_legacy_token() -> None:
    """A pre-two-account install stored its token at data/gmail_token.json."""
    legacy = config.DATA_DIR / "gmail_token.json"
    if legacy.exists() and not config.GMAIL_TOKEN_PERSONAL.exists():
        legacy.rename(config.GMAIL_TOKEN_PERSONAL)


def _load_creds(account: str):
    """Return valid Credentials for the account, refreshing only when expired."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        raise RuntimeError("Run: pip install google-auth google-auth-oauthlib google-api-python-client")

    creds = _creds_cache.get(account)
    if creds is None:
        _migrate_legacy_token()
        token_path = _ACCOUNTS[account]
        if not token_path.exists():
            return None
        creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)
        _creds_cache[account] = creds

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _ACCOUNTS[account].write_text(creds.to_json())

    return creds if creds.valid else None


def is_connected(account: str = "personal") -> bool:
    try:
        return _load_creds(account) is not None
    except Exception:
        return False


def connected_accounts() -> list[str]:
    """Return account keys that have valid tokens."""
    return [name for name in _ACCOUNTS if is_connected(name)]


def get_last_sync() -> Optional[str]:
    return _LAST_SYNC


def get_gmail_service(account: str = "personal"):
    """Return an authorized Gmail API service for the given account."""
    from googleapiclient.discovery import build  # type: ignore

    if account not in _ACCOUNTS:
        raise ValueError(f"Unknown account '{account}'. Choose from: {list(_ACCOUNTS)}")

    creds = _load_creds(account)
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
