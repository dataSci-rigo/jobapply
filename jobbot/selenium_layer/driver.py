"""
Persistent Patchright browser singleton.
Flask runs single-process (threaded=False) so one browser context is safe for the app lifetime.

Session model:
  - One Playwright instance, one Browser, one BrowserContext, one Page.
  - Cookies are saved to data/browser_cookies.json on request and restored at startup.
  - The browser window is visible (headless=False) so the user can watch and interact.
"""

import json
import time
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config

from selenium_layer.field_extractor import FieldExtractor
from selenium_layer.filler import FieldFiller

_COOKIES_PATH = config.DATA_DIR / "browser_cookies.json"

# Module-level singletons
_pw      = None
_browser = None
_context = None
_page    = None


class ProfileInUseError(RuntimeError):
    """CHROME_PROFILE_DIR is locked by another Chrome instance."""


def _profile_holder() -> Optional[str]:
    """
    Return a description of the process already holding CHROME_PROFILE_DIR, or None.

    Chrome locks a whole user-data-dir, so any other tool driving the same profile
    blocks us — and the raw Playwright error ("Opening in existing browser session")
    doesn't say who. Name the culprit so the fix is obvious.
    """
    import subprocess
    try:
        out = subprocess.run(
            ["pgrep", "-af", f"user-data-dir={config.CHROME_PROFILE_DIR}"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return None
    for line in out.splitlines():
        pid, _, cmd = line.partition(" ")
        if "chrome_crashpad" in cmd or "--type=" in cmd:
            continue   # helper/renderer processes, not the session owner
        return f"pid {pid}"
    return None


def _start() -> None:
    global _pw, _browser, _context, _page
    from patchright.sync_api import sync_playwright

    _pw      = sync_playwright().start()
    launch_args = {
        "headless": False,
        "channel": "chrome",   # real Chrome — Patchright's recommended stealth setup
        "args": ["--start-maximized"],
    }
    if config.CHROME_PROFILE_DIR:
        args = ["--start-maximized"]
        # Pick which account's sub-profile inside the user-data-dir to open.
        # A Chrome user-data-dir holds several profiles ("Default", "Profile 1", …),
        # each signed into different Google accounts — this selects one.
        if config.CHROME_PROFILE_DIRECTORY:
            args.append(f"--profile-directory={config.CHROME_PROFILE_DIRECTORY}")
        # Persistent context keeps login sessions across restarts without cookie files
        try:
            _context = _pw.chromium.launch_persistent_context(
                config.CHROME_PROFILE_DIR,
                headless=False,
                channel="chrome",
                args=args,
                no_viewport=True,
            )
        except Exception as exc:
            if "already in use" in str(exc) or "existing browser session" in str(exc):
                holder = _profile_holder()
                _pw.stop()
                _pw = None
                raise ProfileInUseError(
                    f"Chrome profile {config.CHROME_PROFILE_DIR} is already open"
                    + (f" by {holder}" if holder else "")
                    + ". Close that Chrome, or point CHROME_PROFILE_DIR at a "
                      "profile directory of its own for this app."
                ) from exc
            raise
        _browser = None  # persistent context owns the browser
        _page    = _context.pages[0] if _context.pages else _context.new_page()
    else:
        _browser = _pw.chromium.launch(**launch_args)
        _context = _browser.new_context(no_viewport=True)
        _page    = _context.new_page()

    # Restore saved cookies if present
    if _COOKIES_PATH.exists() and not config.CHROME_PROFILE_DIR:
        try:
            cookies = json.loads(_COOKIES_PATH.read_text())
            _context.add_cookies(cookies)
        except Exception:
            pass


def get_page():
    global _page
    if _page is None:
        _start()
    return _page


def get_context():
    if _context is None:
        _start()
    return _context


def navigate(url: str) -> None:
    page = get_page()
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)


def current_url() -> str:
    return get_page().url


def save_cookies() -> int:
    """Persist current browser cookies to disk. Returns count saved."""
    cookies = get_context().cookies()
    _COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _COOKIES_PATH.write_text(json.dumps(cookies))
    return len(cookies)


def load_cookies(cookies: list[dict]) -> None:
    """Inject a list of cookies into the current context."""
    get_context().add_cookies(cookies)


def extract_fields() -> list[dict]:
    return FieldExtractor(get_page()).extract()


def fill_fields(fills: list[dict]) -> list[dict]:
    """
    fills: [{selector, value, field_type}, ...]
    Returns [{selector, ok, error}, ...]
    """
    filler  = FieldFiller(get_page())
    results = []
    for f in fills:
        try:
            filler.fill(f["selector"], f["value"], f.get("field_type", "text"))
            results.append({"selector": f["selector"], "ok": True})
        except Exception as exc:
            results.append({"selector": f["selector"], "ok": False, "error": str(exc)})
    return results


def quit_driver() -> None:
    global _pw, _browser, _context, _page
    try:
        if _context:
            _context.close()
        if _browser:
            _browser.close()
        if _pw:
            _pw.stop()
    except Exception:
        pass
    finally:
        _pw = _browser = _context = _page = None
