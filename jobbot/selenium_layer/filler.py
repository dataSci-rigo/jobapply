"""
Fill form fields in the live Patchright browser window.
Handles: text/textarea, native <select>, custom React/Vue dropdowns,
radio/checkbox, and file inputs.
"""

import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config


class FieldFiller:
    def __init__(self, page, timeout: float = 5_000):
        self.page    = page
        self.timeout = timeout   # milliseconds

    # ── Public ────────────────────────────────────────────────────────────────

    def fill(self, selector: str, value: str, field_type: str = "text") -> None:
        """Fill a single field. Raises on failure."""
        if not selector:
            raise ValueError("Empty selector — cannot fill field.")

        el = self._find(selector)

        if field_type == "file":
            self._fill_file(selector, value)
        elif field_type == "select":
            self._fill_select(el, selector, value)
        elif field_type == "checkbox":
            self._fill_checkbox(el, value)
        elif field_type == "radio":
            el.check()
        else:
            self._fill_text(el, selector, value)

    # ── Strategies ────────────────────────────────────────────────────────────

    def _fill_text(self, el, selector: str, value: str) -> None:
        el.scroll_into_view_if_needed(timeout=self.timeout)
        el.click(timeout=self.timeout)
        # Select-all + type (triple-click selects all text in the field)
        el.triple_click(timeout=self.timeout)
        el.type(value, delay=30)   # delay=30ms mimics human typing pace

        # Trigger React/Vue synthetic change event
        self.page.evaluate(
            """([sel, val]) => {
                const el = document.querySelector(sel);
                if (!el) return;
                const setter = Object.getOwnPropertyDescriptor(
                    el.tagName === 'TEXTAREA'
                        ? HTMLTextAreaElement.prototype
                        : HTMLInputElement.prototype,
                    'value'
                );
                if (setter) setter.set.call(el, val);
                el.dispatchEvent(new Event('input',  {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }""",
            [selector, value],
        )

    def _fill_select(self, el, selector: str, value: str) -> None:
        tag = (el.evaluate("e => e.tagName") or "").lower()

        if tag == "select":
            # Native <select>: try exact text, then partial match
            try:
                el.select_option(label=value, timeout=self.timeout)
                return
            except Exception:
                pass
            # Partial label match
            options = el.evaluate(
                "e => Array.from(e.options).map(o => o.text.trim())"
            )
            match = next((o for o in options if value.lower() in o.lower()), None)
            if match:
                el.select_option(label=match, timeout=self.timeout)
                return
            raise ValueError(f"Option '{value}' not found in <select>.")

        # Custom dropdown (react-select, etc.)
        self._fill_custom_dropdown(el, selector, value)

    def _fill_custom_dropdown(self, el, selector: str, value: str) -> None:
        el.scroll_into_view_if_needed(timeout=self.timeout)
        el.click(timeout=self.timeout)
        time.sleep(0.3)

        # Type into whatever input is now active
        self.page.keyboard.type(value, delay=40)
        time.sleep(0.5)

        # Find a visible option containing the text
        option = self.page.locator(
            f"[role='option'], [class*='option']"
        ).filter(has_text=value).first

        try:
            option.wait_for(state="visible", timeout=self.timeout)
            option.click(timeout=self.timeout)
        except Exception:
            raise ValueError(
                f"Custom dropdown: option matching '{value}' not visible after typing."
            )

    def _fill_checkbox(self, el, value: str) -> None:
        want_checked = value.strip().lower() in ("true", "yes", "1", "on", "checked")
        is_checked   = el.is_checked()
        if want_checked != is_checked:
            el.click(timeout=self.timeout)

    def _fill_file(self, selector: str, absolute_path: str) -> None:
        # Playwright set_input_files handles the file chooser natively
        self.page.set_input_files(selector, absolute_path, timeout=self.timeout)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _find(self, selector: str):
        """Locate element in main page or any iframe, return Playwright Locator."""
        # Try main frame first
        loc = self.page.locator(selector).first
        try:
            loc.wait_for(state="visible", timeout=2_000)
            return loc
        except Exception:
            pass

        # Search through iframes
        for frame in self.page.frames[1:]:   # skip main frame already tried
            try:
                el = frame.locator(selector).first
                el.wait_for(state="visible", timeout=1_000)
                return el
            except Exception:
                continue

        raise Exception(f"Element not found or not visible: {selector}")
