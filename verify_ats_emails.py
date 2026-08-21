#!/usr/bin/env python3
"""
Find ATS "verify your email / activate your account" messages in Gmail and
open each verification link in the shared-profile browser so no account
activation gets missed.

Connects to Gmail over IMAP using email_address/email_password (app password)
from the master .env — no OAuth needed.

Usage (run while the apply2jobs app is stopped — shares the Chrome profile):
    python verify_ats_emails.py            # scan last 3 days, open links
    python verify_ats_emails.py --days 7   # look further back
    python verify_ats_emails.py --list     # just list, don't open anything
"""

import email
import imaplib
import re
import sys
import time
from email.header import decode_header
from pathlib import Path

ENV_FILE = Path(__file__).parent.parent / ".env"
PROFILE_DIR = Path.home() / ".config" / "shared-chrome-profile"

# Senders / subjects that indicate an account-verification email
_SENDER_HINTS = ("myworkday", "workday", "taleo", "successfactors", "sap",
                 "icims", "jobvite", "smartrecruiters", "greenhouse", "lever",
                 "noreply", "no-reply", "donotreply")
_SUBJECT_HINTS = ("verify", "verification", "activate", "confirm", "welcome")

_URL_RE = re.compile(r'https?://[^\s<>"\']+')
# Prefer links that look like verification endpoints
_LINK_HINTS = ("verify", "activate", "confirm", "token", "validat", "activation")


def _read_env(path: Path) -> dict:
    env: dict = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def _decode(value: str) -> str:
    parts = decode_header(value or "")
    out = ""
    for text, charset in parts:
        out += text.decode(charset or "utf-8", errors="replace") if isinstance(text, bytes) else text
    return out


def _body_text(msg) -> str:
    chunks = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() in ("text/plain", "text/html"):
                payload = part.get_payload(decode=True)
                if payload:
                    chunks.append(payload.decode(part.get_content_charset() or "utf-8",
                                                 errors="replace"))
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            chunks.append(payload.decode(msg.get_content_charset() or "utf-8",
                                         errors="replace"))
    return "\n".join(chunks)


def _pick_link(body: str) -> str:
    urls = [u.rstrip(").,>&#39;") for u in _URL_RE.findall(body)]
    for u in urls:                       # first choice: an obvious verify link
        if any(h in u.lower() for h in _LINK_HINTS):
            return u
    for u in urls:                       # fallback: any ATS-domain link
        if any(h in u.lower() for h in ("myworkday", "taleo", "successfactors", "icims")):
            return u
    return ""


def find_verification_emails(days: int) -> list[dict]:
    env = _read_env(ENV_FILE)
    user = env.get("email_address")
    pw = env.get("email_password")
    if not user or not pw:
        sys.exit("email_address/email_password missing from .env")

    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(user, pw)
    imap.select("INBOX")

    since = time.strftime("%d-%b-%Y", time.localtime(time.time() - days * 86400))
    _, data = imap.search(None, f'(SINCE "{since}")')
    ids = data[0].split()

    hits = []
    for mid in reversed(ids):            # newest first
        _, msg_data = imap.fetch(mid, "(RFC822)")
        msg = email.message_from_bytes(msg_data[0][1])
        sender = _decode(msg.get("From", "")).lower()
        subject = _decode(msg.get("Subject", ""))
        if not any(h in sender for h in _SENDER_HINTS):
            continue
        if not any(h in subject.lower() for h in _SUBJECT_HINTS):
            continue
        link = _pick_link(_body_text(msg))
        hits.append({"from": _decode(msg.get("From", "")), "subject": subject,
                     "date": msg.get("Date", ""), "link": link})
    imap.logout()
    return hits


def main() -> None:
    days = 3
    list_only = "--list" in sys.argv
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])

    print(f"[verify] Scanning INBOX (last {days} days) for ATS verification emails…")
    hits = find_verification_emails(days)

    if not hits:
        print("[verify] No verification emails found. Nothing to click.")
        return

    print(f"[verify] {len(hits)} candidate email(s):")
    for h in hits:
        mark = "→" if h["link"] else "⚠ no link found"
        print(f"  {h['date'][:22]:22s} {h['from'][:45]:46s} {h['subject'][:50]}")
        if h["link"]:
            print(f"      {mark} {h['link'][:110]}")

    links = [h["link"] for h in hits if h["link"]]
    if list_only or not links:
        if not list_only:
            print("[verify] No clickable links extracted — open the emails manually.")
        return

    print(f"\n[verify] Opening {len(links)} link(s) in the shared-profile browser…")
    from patchright.sync_api import sync_playwright
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False,
            args=["--start-maximized"], no_viewport=True)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for i, link in enumerate(links, 1):
            print(f"  [{i}/{len(links)}] {link[:100]}")
            try:
                page.goto(link, wait_until="domcontentloaded", timeout=30_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8_000)
                except Exception:
                    pass
                time.sleep(1.5)
                print(f"      landed: {page.url[:100]}")
            except Exception as exc:
                print(f"      ERROR: {exc}")
        input("\nReview the browser (some pages may ask you to sign in), then press Enter to close… ")
        ctx.close()


if __name__ == "__main__":
    main()
