#!/usr/bin/env python3
"""
Create candidate accounts on ATS portals (Workday, SuccessFactors, Taleo, …).

Each company runs its own portal (its own Workday tenant, its own Taleo org),
so applying broadly means dozens of accounts. This script automates the
sign-up: for every portal URL you give it, it detects the ATS, creates an
account with your email + a freshly generated strong password, and stores the
credentials in the app's encrypted vault (jobbot/data/credentials.json) so
auto_login can use them later.

It NEVER submits an application — accounts only.

Usage (run while the apply2jobs app is stopped — they share the Chrome profile):
    python setup_ats_accounts.py <portal-or-job-url> [more urls...]
    python setup_ats_accounts.py --file ats_portals.txt
    python setup_ats_accounts.py --yes ...   # non-interactive: skip pauses,
                                             # report blockers instead

Interactive pauses happen on CAPTCHA or email-verification steps.
Passwords: 16-char random (letters/digits/symbols), one per portal, shown once
here and kept in the vault. Retrieve later via the app's /credentials page.
"""

import re
import secrets
import string
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent / "jobbot"))
import credentials as cred_store  # noqa: E402  (vault + _try_selector reuse)

PROFILE_DIR = Path.home() / ".config" / "shared-chrome-profile"
ENV_FILE = Path(__file__).parent.parent / ".env"

_try = cred_store._try_selector
_settle = cred_store._settle


def _read_env(path: Path) -> dict:
    env: dict = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def _gen_password() -> str:
    # Portals commonly require upper+lower+digit+symbol; guarantee each class.
    alphabet = string.ascii_letters + string.digits + "!@#$%^*"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(16))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw) and any(c in "!@#$%^*" for c in pw)):
            return pw


def _detect_ats(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if "myworkdayjobs.com" in host or "myworkdaysite.com" in host:
        return "workday"
    if "taleo.net" in host:
        return "taleo"
    if "successfactors" in host or host == "jobs.sap.com" or "sapsf" in host:
        return "successfactors"
    if "icims.com" in host:
        return "icims"
    return "unknown"


def _portal_root(url: str, ats: str) -> str:
    """Vault key: the portal prefix that auto_login will match against."""
    p = urlparse(url)
    if ats == "workday":
        # Tenant + career-site path segment, e.g.
        # https://autodesk.wd1.myworkdayjobs.com/en-US/Ext
        parts = [seg for seg in p.path.split("/") if seg]
        keep = parts[:2] if len(parts) >= 2 and parts[0].lower().startswith("en-") else parts[:1]
        return f"{p.scheme}://{p.netloc}/" + "/".join(keep)
    return f"{p.scheme}://{p.netloc}"


def _pause(msg: str, auto: bool) -> bool:
    """Interactive pause; in --yes mode just report and skip. Returns True if paused."""
    if auto:
        print(f"  BLOCKED (needs a human): {msg}")
        return False
    print(f"  {msg}")
    input("  Press Enter here when done... ")
    return True


# ── Workday ────────────────────────────────────────────────────────────────────

def create_workday_account(page, url: str, email: str, password: str, auto: bool) -> dict:
    root = _portal_root(url, "workday")
    login_url = f"{root}/login"
    page.goto(login_url, wait_until="domcontentloaded", timeout=30_000)
    _settle(page)

    # Already signed in? Workday redirects to userHome / shows the account menu.
    if "userHome" in page.url or _try(page, ['[data-automation-id="accountSettingsButton"]'], timeout=2_000):
        return {"ok": True, "existing": True}

    create_link = _try(page, [
        '[data-automation-id="createAccountLink"]',
        'button:has-text("Create Account")',
        'a:has-text("Create Account")',
    ], timeout=6_000)
    if create_link:
        create_link.click()
        _settle(page)

    email_el = _try(page, ['[data-automation-id="email"]', 'input[type="email"]'], timeout=6_000)
    pw_el    = _try(page, ['[data-automation-id="password"]'], timeout=3_000)
    verify_el = _try(page, ['[data-automation-id="verifyPassword"]'], timeout=3_000)
    if not email_el or not pw_el:
        return {"ok": False, "error": "Create-account form not found (page layout changed?)"}

    email_el.fill(email)
    pw_el.fill(password)
    if verify_el:
        verify_el.fill(password)

    # Privacy / terms checkbox: Workday hides the real <input> behind a styled
    # widget, so force-check it and fall back to clicking the label wrapper.
    for sel in ('[data-automation-id="createAccountCheckbox"]', 'input[type="checkbox"]'):
        try:
            cb = page.locator(sel).first
            if cb.count() == 0:
                continue
            try:
                cb.check(force=True, timeout=3_000)
            except Exception:
                cb.evaluate("el => { el.click(); }")
            break
        except Exception:
            continue

    # Workday overlays a click_filter div over its buttons — that div is the
    # real click target; the button itself "intercepts pointer events" forever.
    submit = _try(page, [
        'div[data-automation-id="click_filter"]',
        '[data-automation-id="createAccountSubmitButton"]',
        'button[type="submit"]',
        'button:has-text("Create Account")',
    ], timeout=3_000)
    if not submit:
        return {"ok": False, "error": "Submit button not found"}
    try:
        submit.click(timeout=8_000)
    except Exception:
        # Button still reported un-clickable (disabled/overlay) — try force,
        # then Enter as a last resort.
        try:
            submit.click(force=True, timeout=5_000)
        except Exception:
            page.keyboard.press("Enter")
    _settle(page, timeout=15_000)
    time.sleep(2)

    # Error banner (already exists, password policy, …)
    err = _try(page, ['[data-automation-id="errorMessage"]', '[data-automation-id="alertMessage"]'],
               timeout=2_000)
    if err:
        try:
            msg = err.inner_text()[:160]
        except Exception:
            msg = "portal rejected the sign-up"
        if "already" in msg.lower():
            return {"ok": True, "existing": True, "note": msg}
        return {"ok": False, "error": msg}

    # Email verification step?
    if _try(page, ['[data-automation-id="verificationCode"]', 'input[name*="verif"]'], timeout=2_000):
        _pause("Verification code sent to your email — enter it in the browser.", auto)

    return {"ok": True, "created": True}


# ── SuccessFactors ─────────────────────────────────────────────────────────────

def create_successfactors_account(page, url: str, email: str, password: str, auto: bool) -> dict:
    # Career sites vary; the standard RCM flow is careers page → Sign In → Create.
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    _settle(page)

    apply_btn = _try(page, ['a[title*="Apply"]', 'a:has-text("Apply now")',
                            'button:has-text("Apply now")'], timeout=5_000)
    if apply_btn:
        apply_btn.click()
        _settle(page)

    create = _try(page, ['a:has-text("Create an account")', 'a:has-text("Create Account")',
                         '#careersignup', 'a[id*="register"]'], timeout=5_000)
    if create:
        create.click()
        _settle(page)

    email_el = _try(page, ['input[type="email"]', 'input[id*="email" i]'], timeout=5_000)
    pw_els = page.locator('input[type="password"]')
    if not email_el or pw_els.count() == 0:
        return {"ok": False,
                "error": "Sign-up form not found — this SF career site may use a custom flow. Finish manually."}

    email_el.fill(email)
    for i in range(min(pw_els.count(), 2)):     # password + confirm
        pw_els.nth(i).fill(password)

    _pause("Review the SF sign-up form in the browser (extra required fields / consent), "
           "then click its Create/Register button yourself.", auto)
    return {"ok": True, "created": True, "note": "verify manually — SF flows vary per company"}


# ── Taleo ──────────────────────────────────────────────────────────────────────

def create_taleo_account(page, url: str, email: str, password: str, auto: bool) -> dict:
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    _settle(page)

    apply_btn = _try(page, ['a:has-text("Apply")', 'button:has-text("Apply")',
                            '#requisitionDescriptionInterface\\.pagerDivID1417\\.Apply'], timeout=5_000)
    if apply_btn:
        apply_btn.click()
        _settle(page)

    # Taleo Enterprise: "New user" registration
    new_user = _try(page, ['a:has-text("New User")', 'button:has-text("New User")',
                           '#dialogTemplate-dialogForm-login-name2'], timeout=5_000)
    if new_user:
        new_user.click()
        _settle(page)
        user_el = _try(page, ['input[id*="username" i]', 'input[name*="username" i]'], timeout=4_000)
        pw_els = page.locator('input[type="password"]')
        email_el = _try(page, ['input[id*="email" i]', 'input[type="email"]'], timeout=2_000)
        if user_el:
            user_el.fill(email.split("@")[0] + "_" + secrets.token_hex(2))
        if email_el:
            email_el.fill(email)
        for i in range(min(pw_els.count(), 2)):
            pw_els.nth(i).fill(password)
        _pause("Review the Taleo registration form, complete any extra fields, and click Register.", auto)
        return {"ok": True, "created": True, "note": "verify manually — Taleo flows vary per company"}

    # Taleo Business Edition often applies without an account
    if _try(page, ['input[name="firstName"]', '#firstName'], timeout=3_000):
        return {"ok": True, "existing": False,
                "note": "This Taleo BE portal has a direct application form — no account needed."}

    return {"ok": False, "error": "Could not find Taleo apply/registration entry point. Finish manually."}


_FLOWS = {
    "workday": create_workday_account,
    "successfactors": create_successfactors_account,
    "taleo": create_taleo_account,
}


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    args = [a for a in sys.argv[1:]]
    auto = "--yes" in args
    args = [a for a in args if a != "--yes"]

    urls: list[str] = []
    if "--file" in args:
        i = args.index("--file")
        urls += [l.strip() for l in Path(args[i + 1]).read_text().splitlines()
                 if l.strip() and not l.strip().startswith("#")]
        args = args[:i] + args[i + 2:]
    urls += args

    if not urls:
        print(__doc__)
        sys.exit(1)

    env = _read_env(ENV_FILE)
    email = env.get("GOOGLE_EMAIL", "rluna727@gmail.com")

    from patchright.sync_api import sync_playwright

    results = []
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False,
            args=["--start-maximized"], no_viewport=True,
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        for url in urls:
            ats = _detect_ats(url)
            root = _portal_root(url, ats)
            print(f"\n[{ats:>15s}] {root}")

            if ats == "unknown":
                results.append((root, "SKIP", "unrecognised ATS"))
                print("  SKIP — unrecognised ATS host")
                continue

            existing = cred_store.find_credential(root)
            if existing:
                results.append((root, "OK", "already in vault"))
                print(f"  Already in vault ({existing['username']}) — skipping.")
                continue

            password = _gen_password()
            try:
                res = _FLOWS[ats](page, url, email, password, auto)
            except Exception as exc:
                res = {"ok": False, "error": str(exc)[:160]}

            if res.get("ok") and (res.get("created") or res.get("existing")):
                if res.get("created"):
                    cred_store.add_credential(
                        root, email, password,
                        notes=f"ATS account ({ats}), created {date.today().isoformat()}",
                    )
                    print(f"  Account created — stored in vault. Password: {password}")
                else:
                    print(f"  Account already exists on the portal — add its password to the "
                          f"vault via /credentials if you know it. {res.get('note', '')}")
                results.append((root, "OK", res.get("note", "")))
            elif res.get("ok"):
                results.append((root, "OK", res.get("note", "")))
                print(f"  {res.get('note', 'done')}")
            else:
                results.append((root, "FAIL", res.get("error", "")))
                print(f"  FAIL — {res.get('error')}")

        print("\n── Summary ──")
        for root, status, note in results:
            print(f"  [{status:4s}] {root}  {note}")

        if not auto:
            input("\nPress Enter to close the browser... ")
        ctx.close()


if __name__ == "__main__":
    main()
