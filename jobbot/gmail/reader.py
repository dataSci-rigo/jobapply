"""
Fetch recent recruiter-style emails from Gmail — checks both accounts.
"""

import base64
import sys
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from gmail.oauth import get_all_gmail_services

_RECRUITER_QUERY = (
    "from:(-me) "
    "subject:(offer OR interview OR application OR position OR role OR opportunity OR "
    "decision OR update OR next steps OR assessment OR rejected OR unfortunately)"
)


def _decode_body(payload: dict) -> str:
    parts = payload.get("parts", [])
    for part in parts:
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    data = payload.get("body", {}).get("data", "")
    if data:
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    return ""


def _header(headers: list, name: str) -> str:
    name_lower = name.lower()
    for h in headers:
        if h["name"].lower() == name_lower:
            return h["value"]
    return ""


def _date_sort_key(email: dict) -> float:
    try:
        return parsedate_to_datetime(email["date"]).timestamp()
    except Exception:
        return 0.0


def _fetch_from_service(service, days: int, account_label: str) -> list[dict]:
    query = f"{_RECRUITER_QUERY} newer_than:{days}d"
    results = service.users().messages().list(
        userId="me", q=query, maxResults=100
    ).execute()
    msg_refs = results.get("messages", [])
    if not msg_refs:
        return []

    emails = []

    def _collect(request_id, msg, exception):
        if exception is not None or not msg:
            return
        headers = msg.get("payload", {}).get("headers", [])
        body    = _decode_body(msg.get("payload", {}))
        snippet = msg.get("snippet", "")
        emails.append({
            "message_id": msg["id"],
            "subject":    _header(headers, "Subject"),
            "sender":     _header(headers, "From"),
            "snippet":    (body[:400] if body else snippet[:400]),
            "date":       _header(headers, "Date"),
            "account":    account_label,
        })

    # One batched HTTP request per 100 messages instead of one round-trip each
    batch = service.new_batch_http_request(callback=_collect)
    for msg_ref in msg_refs:
        batch.add(service.users().messages().get(
            userId="me", id=msg_ref["id"], format="full"
        ))
    batch.execute()
    return emails


def fetch_recent_recruiter_emails(days: int = 14) -> list[dict]:
    """Return recruiter emails from all connected Gmail accounts, newest first."""
    services = get_all_gmail_services()
    if not services:
        raise RuntimeError(
            "No Gmail accounts connected. Run: python setup_gmail_oauth.py"
        )

    all_emails = []
    errors: dict[str, str] = {}
    for account_name, service in services.items():
        try:
            all_emails.extend(_fetch_from_service(service, days, account_name))
        except Exception as exc:
            errors[account_name] = str(exc)
            print(f"[gmail] WARNING: sync failed for '{account_name}': {exc}")

    if errors and len(errors) == len(services):
        raise RuntimeError(f"Gmail sync failed for all accounts: {errors}")

    all_emails.sort(key=_date_sort_key, reverse=True)
    return all_emails
