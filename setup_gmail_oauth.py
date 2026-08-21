#!/usr/bin/env python3
"""
One-time Gmail OAuth setup for apply2jobs.

Authorises two Google accounts for Gmail readonly access:
  1. rluna727@gmail.com   → data/gmail_token_personal.json
  2. rlunaorozco@berkeley.edu → data/gmail_token_berkeley.json

Reads GOOGLE_CLIENT_SECRET from .env for the OAuth client credentials.

Run this on the laptop (needs a browser). The VM gets the minted token files by
copying jobbot/data/gmail_token_*.json over — they are gitignored, so git will
not carry them.

Usage:
    conda run -n jobapply python setup_gmail_oauth.py
"""

import shutil
import sys
from pathlib import Path

# Master .env lives one level up (Documents root); fall back to a local .env
ENV_FILE = Path(__file__).parent.parent / ".env"
if not ENV_FILE.exists():
    ENV_FILE = Path(__file__).parent / ".env"
PROJECT_DATA = Path(__file__).parent / "jobbot" / "data"

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
]

ACCOUNTS = [
    ("personal",  "rluna727@gmail.com",          "gmail_token_personal.json"),
    ("berkeley",  "rlunaorozco@berkeley.edu",     "gmail_token_berkeley.json"),
]


def _read_env(path: Path) -> dict:
    env: dict = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip()
    return env


def _auth_account(client_secret: Path, token_path: Path, hint_email: str, label: str) -> bool:
    """Run InstalledAppFlow for one account, save token. Returns True on success."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
    except ImportError:
        print("ERROR: google-auth-oauthlib not installed.")
        print("       conda run -n jobapply pip install google-auth-oauthlib google-api-python-client")
        return False

    print(f"\n  Opening browser for {hint_email} ...")
    print(f"  Make sure to sign in as {hint_email} when Google asks.")

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
    # run_local_server opens a browser and spins up a local redirect handler
    creds = flow.run_local_server(port=0, open_browser=True, login_hint=hint_email)

    token_path.write_text(creds.to_json())
    print(f"  Token saved → {token_path}")
    return True


def main() -> None:
    env = _read_env(ENV_FILE)

    client_secret_src = env.get("GOOGLE_CLIENT_SECRET", "")
    if not client_secret_src:
        print("ERROR: GOOGLE_CLIENT_SECRET not set in .env")
        sys.exit(1)

    client_secret_src = Path(client_secret_src)
    if not client_secret_src.exists():
        print(f"ERROR: client secret not found at {client_secret_src}")
        sys.exit(1)

    PROJECT_DATA.mkdir(parents=True, exist_ok=True)

    # Copy client secret into the project data dir
    dest_creds = PROJECT_DATA / "gmail_credentials.json"
    shutil.copy2(client_secret_src, dest_creds)
    print(f"[setup] Client secret → {dest_creds}")

    print(f"\n[setup] Authorising {len(ACCOUNTS)} Gmail account(s).")
    print("[setup] A browser tab will open for each. Sign in and click Allow.")
    print("[setup] Berkeley may require CalNet + DUO before reaching the consent screen.\n")

    results = {}
    for label, email, token_filename in ACCOUNTS:
        token_path = PROJECT_DATA / token_filename
        if token_path.exists():
            print(f"[{label}] Token already exists — skipping. Delete {token_path} to re-auth.")
            results[label] = True
            continue

        print(f"[{label}] Authorising {email} ...")
        ok = _auth_account(dest_creds, token_path, email, label)
        results[label] = ok

    print("\n── Summary ──")
    for label, ok in results.items():
        status = "OK" if ok else "FAILED"
        print(f"  [{status}] {label}")

    if all(results.values()):
        print("\nBoth accounts authorised. The apply2jobs Gmail sync is ready.")
        print("Trigger a sync from the app at /gmail, or POST /api/gmail/sync")
    else:
        print("\nSome accounts failed. Re-run to retry failed accounts.")


if __name__ == "__main__":
    main()
