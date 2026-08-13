"""
Fetch recent recruiter-style emails from Gmail — checks both accounts.
"""

import base64
import sys
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


def _fetch_from_service(service, days: int, account_label: str) -> list[dict]:
    query = f"{_RECRUITER_QUERY} newer_than:{days}d"
    results = service.users().messages().list(
        userId="me", q=query, maxResults=100
    ).execute()

    emails = []
    for msg_ref in results.get("messages", []):
        try:
            msg = service.users().messages().get(
                userId="me", id=msg_ref["id"], format="full"
            ).execute()
            headers = msg.get("payload", {}).get("headers", [])
            subject = _header(headers, "Subject")
            sender  = _header(headers, "From")
            snippet = msg.get("snippet", "")
            body    = _decode_body(msg.get("payload", {}))
            emails.append({
                "message_id": f"{account_label}:{msg_ref['id']}",
                "subject":    subject,
                "sender":     sender,
                "snippet":    (body[:400] if body else snippet[:400]),
                "date":       _header(headers, "Date"),
                "account":    account_label,
            })
        except Exception:
            continue
    return emails


def fetch_recent_recruiter_emails(days: int = 14) -> list[dict]:
    """Return recruiter emails from all connected Gmail accounts, newest first."""
    services = get_all_gmail_services()
    if not services:
        raise RuntimeError(
            "No Gmail accounts connected. Run: python setup_gmail_oauth.py"
        )

    all_emails = []
    for account_name, service in services.items():
        try:
            emails = _fetch_from_service(service, days, account_name)
            all_emails.extend(emails)
        except Exception:
            continue

    return all_emails
