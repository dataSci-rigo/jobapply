"""
Fetch recent recruiter-style emails from Gmail.
"""

import base64
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from gmail.oauth import get_gmail_service

_RECRUITER_QUERY = (
    "from:(-me) "
    "subject:(offer OR interview OR application OR position OR role OR opportunity OR "
    "decision OR update OR next steps OR assessment OR rejected OR unfortunately)"
)


def _decode_body(payload: dict) -> str:
    """Extract plain-text snippet from a message payload."""
    parts = payload.get("parts", [])
    for part in parts:
        if part.get("mimeType") == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    # Fallback: top-level body
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


def fetch_recent_recruiter_emails(days: int = 14) -> list[dict]:
    """Return list of {message_id, subject, sender, snippet, date} dicts."""
    service = get_gmail_service()
    query = f"{_RECRUITER_QUERY} newer_than:{days}d"
    results = service.users().messages().list(
        userId="me", q=query, maxResults=100
    ).execute()

    messages = results.get("messages", [])
    emails = []
    for msg_ref in messages:
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
                "message_id": msg_ref["id"],
                "subject": subject,
                "sender": sender,
                "snippet": (body[:400] if body else snippet[:400]),
                "date": _header(headers, "Date"),
            })
        except Exception:
            continue
    return emails
