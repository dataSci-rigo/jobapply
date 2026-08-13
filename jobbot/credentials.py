"""
Encrypted credential store + per-site auto-login using Patchright.

Key file : data/.secret_key  (chmod 600, never commit)
Vault    : data/credentials.json  (ciphertext — safe to back up)
Cookies  : data/browser_cookies.json  (written by driver.save_cookies())

Supported sites and their login selectors are defined in SITE_CONFIGS below.
"""

import json
import os
import stat
import time
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet

import sys
sys.path.insert(0, str(Path(__file__).parent))
import config

_KEY_PATH   = config.DATA_DIR / ".secret_key"
_VAULT_PATH = config.DATA_DIR / "credentials.json"

_fernet: Optional[Fernet] = None

# ── Per-site login config ──────────────────────────────────────────────────────
# Each entry: url_prefix → dict of selectors and behaviour flags.
# Selectors are tried in order; first visible match wins.

SITE_CONFIGS = {
    "https://www.linkedin.com": {
        "name":       "LinkedIn",
        "login_url":  "https://www.linkedin.com/login",
        "username":   ["#username", 'input[name="session_key"]', 'input[autocomplete="username"]'],
        "password":   ["#password", 'input[name="session_password"]', 'input[type="password"]'],
        "submit":     ['button[type="submit"]', '.login__form_action_container button'],
        "logged_in_check": "https://www.linkedin.com/feed",
        "otp_selectors": [
            "#input__phone_verification_pin",
            "#input__email_verification_pin",
            'input[name="pin"]',
            'input[autocomplete="one-time-code"]',
            'input[id*="verification"]',
            'input[id*="pin"]',
        ],
        "otp_submit": ['button[type="submit"]', '#two-step-submit-button'],
    },
    "https://secure.indeed.com": {
        "name":       "Indeed",
        "login_url":  "https://secure.indeed.com/auth",
        "username":   ['input[name="email"]', '#ifl-InputFormField-3', 'input[autocomplete="email"]'],
        "password":   ['input[type="password"]'],
        "submit":     ['button[type="submit"]'],
        "logged_in_check": "https://www.indeed.com",
        "otp_selectors": ['input[name="code"]', 'input[autocomplete="one-time-code"]'],
        "otp_submit":    ['button[type="submit"]'],
        "sso_google":    [
            '[data-tn-element="google-auth-button"]',
            '.icl-SocialLoginButton--google',
            'a[aria-label*="Google"]',
            'button[aria-label*="Google"]',
            'a[data-tn-element*="google"]',
        ],
    },
    "https://www.glassdoor.com": {
        "name":       "Glassdoor",
        "login_url":  "https://www.glassdoor.com/profile/login_input.htm",
        "username":   ['input[name="username"]', '#inlineUserEmail', 'input[autocomplete="email"]'],
        "password":   ['input[type="password"]', 'input[name="password"]'],
        "submit":     ['button[type="submit"]', '#loginBtn'],
        "logged_in_check": "https://www.glassdoor.com/member/home",
        "otp_selectors": ['input[autocomplete="one-time-code"]', 'input[name="code"]'],
        "otp_submit":    ['button[type="submit"]'],
    },
    "https://wellfound.com": {
        "name":       "Wellfound",
        "login_url":  "https://wellfound.com/login",
        "username":   ['input[name="email"]', 'input[type="email"]'],
        "password":   ['input[type="password"]'],
        "submit":     ['button[type="submit"]'],
        "logged_in_check": "https://wellfound.com/jobs",
        "otp_selectors": ['input[autocomplete="one-time-code"]'],
        "otp_submit":    ['button[type="submit"]'],
    },
    "https://app.joinhandshake.com": {
        "name":       "Handshake",
        "login_url":  "https://app.joinhandshake.com/login",
        "username":   ['input[name="email"]', 'input[type="email"]'],
        "password":   ['input[type="password"]'],
        "submit":     ['button[type="submit"]'],
        "logged_in_check": "https://app.joinhandshake.com/stu/jobs",
        "otp_selectors": ['input[autocomplete="one-time-code"]'],
        "otp_submit":    ['button[type="submit"]'],
    },
}

# URL prefixes that need login (for find_site_config lookup)
_SITE_PREFIXES = list(SITE_CONFIGS.keys())


def find_site_config(url: str) -> Optional[dict]:
    """Return the SITE_CONFIG entry whose key is a prefix of the given URL."""
    for prefix, cfg in SITE_CONFIGS.items():
        if url.startswith(prefix):
            return cfg
    return None


# ── Key management ─────────────────────────────────────────────────────────────

def _get_fernet() -> Fernet:
    global _fernet
    if _fernet:
        return _fernet
    if _KEY_PATH.exists():
        key = _KEY_PATH.read_bytes()
    else:
        key = Fernet.generate_key()
        _KEY_PATH.write_bytes(key)
        os.chmod(_KEY_PATH, stat.S_IRUSR | stat.S_IWUSR)
        print(f"[credentials] New encryption key created at {_KEY_PATH}")
    _fernet = Fernet(key)
    return _fernet


# ── Vault I/O ──────────────────────────────────────────────────────────────────

def _load_vault() -> list[dict]:
    if not _VAULT_PATH.exists():
        return []
    raw = _VAULT_PATH.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    entries = []
    for item in json.loads(raw):
        try:
            plaintext = _get_fernet().decrypt(item["data"].encode()).decode()
            entries.append(json.loads(plaintext))
        except Exception:
            pass
    return entries


def _save_vault(entries: list[dict]) -> None:
    encrypted = []
    for entry in entries:
        ciphertext = _get_fernet().encrypt(json.dumps(entry).encode()).decode()
        encrypted.append({"site": entry["site"], "data": ciphertext})
    _VAULT_PATH.write_text(json.dumps(encrypted, indent=2), encoding="utf-8")


# ── Public API ─────────────────────────────────────────────────────────────────

def list_credentials() -> list[dict]:
    return [
        {"id": i, "site": e["site"], "username": e["username"], "notes": e.get("notes", "")}
        for i, e in enumerate(_load_vault())
    ]


def add_credential(site: str, username: str, password: str, notes: str = "") -> None:
    entries = [e for e in _load_vault() if e["site"].rstrip("/") != site.rstrip("/")]
    entries.append({"site": site.rstrip("/"), "username": username,
                    "password": password, "notes": notes})
    _save_vault(entries)


def delete_credential(index: int) -> bool:
    entries = _load_vault()
    if 0 <= index < len(entries):
        entries.pop(index)
        _save_vault(entries)
        return True
    return False


def find_credential(url: str) -> Optional[dict]:
    for entry in _load_vault():
        if url.startswith(entry["site"]):
            return entry
    return None


# ── Patchright auto-login ──────────────────────────────────────────────────────

def _try_selector(page, selectors: list[str], timeout: int = 3_000):
    """Return first visible element matching any selector, or None."""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=timeout)
            return loc
        except Exception:
            continue
    return None


def auto_login(page, url: str) -> dict:
    """
    Navigate to url and fill the login form using saved credentials.

    Returns:
        {"ok": True, "logged_in": True}             — success
        {"ok": True, "needs_otp": True, "url": ...} — hit 2FA wall
        {"ok": False, "error": "..."}               — no creds or form not found

    Raises nothing — all exceptions captured into the return dict.
    """
    cred = find_credential(url)
    if not cred:
        return {"ok": False, "error": f"No saved credentials for {url}"}

    cfg = find_site_config(url)
    if cfg is None:
        # Generic fallback selectors
        cfg = {
            "login_url":  url,
            "username":   ['input[type="email"]', 'input[name="email"]',
                           'input[name="username"]', 'input[autocomplete="email"]'],
            "password":   ['input[type="password"]'],
            "submit":     ['button[type="submit"]', 'input[type="submit"]'],
            "logged_in_check": None,
            "otp_selectors": ['input[autocomplete="one-time-code"]'],
            "otp_submit":    ['button[type="submit"]'],
        }

    try:
        page.goto(cfg["login_url"], wait_until="domcontentloaded", timeout=30_000)
        time.sleep(1.0)

        user_el = _try_selector(page, cfg["username"])
        pass_el = _try_selector(page, cfg["password"])

        if not user_el or not pass_el:
            return {"ok": False,
                    "error": "Could not find username/password fields — log in manually."}

        user_el.triple_click()
        user_el.type(cred["username"], delay=40)
        time.sleep(0.2)
        pass_el.triple_click()
        pass_el.type(cred["password"], delay=40)
        time.sleep(0.2)

        submit_el = _try_selector(page, cfg["submit"])
        if submit_el:
            submit_el.click()
        else:
            pass_el.press("Enter")

        # Wait for navigation
        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

        cur = page.url
        # 2FA / checkpoint detection
        if any(k in cur for k in ("checkpoint", "challenge", "two-step",
                                   "add-phone", "authwall", "verification",
                                   "security", "otp")):
            return {"ok": True, "needs_otp": True, "url": cur,
                    "site_name": cfg.get("name", url)}

        # Still on login page?
        if _try_selector(page, cfg["username"], timeout=1_500):
            return {"ok": False, "error": "Login failed — check username and password."}

        return {"ok": True, "logged_in": True, "site_name": cfg.get("name", url)}

    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def google_sso_login(page, url: str) -> dict:
    """
    Log into a site using "Continue with Google" SSO.

    Requires the browser profile to already be signed into Google
    (works automatically when CHROME_PROFILE_DIR points at a persistent profile).

    Flow:
      1. Navigate to the site's login page.
      2. Click the "Continue with Google" button.
      3. If Google opens in a popup: wait for the popup to close (Google completes
         the consent/account-picker step and redirects back).
      4. If Google redirects in the same tab: wait for networkidle.
      5. Detect success or failure.

    Returns:
        {"ok": True, "logged_in": True, "site_name": ...}    — success
        {"ok": True, "needs_account": True, "url": ...}      — Google account picker
                                                               still open (rare)
        {"ok": False, "error": "..."}                        — site not supported /
                                                               button not found / timeout
    """
    cfg = find_site_config(url)
    if cfg is None:
        return {"ok": False, "error": f"No site config for {url}. Google SSO not supported here."}

    sso_sels = cfg.get("sso_google")
    if not sso_sels:
        return {"ok": False, "error": f"{cfg.get('name', url)} does not have a Google SSO button configured."}

    try:
        page.goto(cfg["login_url"], wait_until="domcontentloaded", timeout=30_000)
        time.sleep(1.5)

        google_btn = _try_selector(page, sso_sels, timeout=5_000)
        if not google_btn:
            return {"ok": False,
                    "error": "Could not find 'Continue with Google' button — the login page may have changed."}

        # Google SSO can open in a popup (most common) or redirect in the same tab.
        # Use expect_popup with a short timeout; fall back to same-tab redirect.
        try:
            with page.expect_popup(timeout=6_000) as popup_info:
                google_btn.click()
            popup = popup_info.value

            # Wait up to 60 s for the Google popup to finish and close itself.
            # If the user is already signed in and has previously consented,
            # the popup closes automatically. Otherwise they may need to pick an account.
            try:
                popup.wait_for_event("close", timeout=60_000)
            except Exception:
                # Popup is still open — probably the account picker
                cur_popup = popup.url
                return {
                    "ok": True,
                    "needs_account": True,
                    "url": cur_popup,
                    "msg": "Google account picker is open — select your account in the browser window.",
                }

        except Exception:
            # No popup appeared — Google may be redirecting in the same tab
            try:
                page.wait_for_load_state("networkidle", timeout=30_000)
            except Exception:
                pass

        # Wait for the site to finish loading after Google hands back control
        time.sleep(1.5)
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass

        cur = page.url
        site_name = cfg.get("name", url)

        # If still on the login page, SSO didn't complete
        if google_btn and _try_selector(page, sso_sels, timeout=1_500):
            return {"ok": False,
                    "error": f"Still on {site_name} login page — Google SSO did not complete."}

        return {"ok": True, "logged_in": True, "site_name": site_name, "url": cur}

    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def submit_otp(page, url: str, otp: str) -> dict:
    """Submit a one-time code on whichever OTP form is currently showing."""
    cfg = find_site_config(url) or {}
    otp_selectors = cfg.get("otp_selectors", ['input[autocomplete="one-time-code"]',
                                               'input[name="pin"]', 'input[name="code"]'])
    submit_sels   = cfg.get("otp_submit",    ['button[type="submit"]'])

    try:
        pin_el = _try_selector(page, otp_selectors)
        if not pin_el:
            return {"ok": False, "error": "Could not find OTP input field."}

        pin_el.triple_click()
        pin_el.type(otp, delay=60)
        time.sleep(0.2)

        submit_el = _try_selector(page, submit_sels)
        if submit_el:
            submit_el.click()
        else:
            pin_el.press("Enter")

        try:
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

        cur = page.url
        if any(k in cur for k in ("checkpoint", "challenge", "two-step", "verification", "otp")):
            return {"ok": True, "needs_otp": True, "url": cur,
                    "msg": "Code may be wrong or another step is required."}

        return {"ok": True, "logged_in": True}

    except Exception as exc:
        return {"ok": False, "error": str(exc)}
