"""
Generic, site-agnostic form field extractor using Patchright (Playwright).
Works across main frame and all iframes via Playwright's frame tree.
"""

import re
from typing import Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from matching.question_match import get_previous_answers


class FieldExtractor:
    def __init__(self, page):
        self.page = page

    # ── Public ────────────────────────────────────────────────────────────────

    def extract(self) -> list[dict]:
        """Extract fields from the main frame and every iframe."""
        fields = []
        seen   = set()

        # Main frame + all child frames
        for frame in self.page.frames:
            try:
                frame_fields = self._extract_in_frame(frame)
                for f in frame_fields:
                    if f["selector"] and f["selector"] not in seen:
                        seen.add(f["selector"])
                        fields.append(f)
            except Exception:
                continue

        return fields

    # ── Private ───────────────────────────────────────────────────────────────

    def _extract_in_frame(self, frame) -> list[dict]:
        fields = []
        try:
            elements = frame.query_selector_all("input, textarea, select")
        except Exception:
            return []

        for el in elements:
            try:
                f = self._describe(el, frame)
            except Exception:
                continue
            if f is None:
                continue
            if f.get("label"):
                f["previous_answers"] = get_previous_answers(f["label"], f["field_type"])
            else:
                f["previous_answers"] = []
            fields.append(f)

        return fields

    def _describe(self, el, frame) -> Optional[dict]:
        tag      = (el.evaluate("e => e.tagName") or "").lower()
        el_type  = (el.get_attribute("type") or "text").lower()
        el_name  = el.get_attribute("name") or ""
        el_id    = el.get_attribute("id") or ""
        maxlen   = el.get_attribute("maxlength")
        required = el.get_attribute("required") is not None

        # Skip non-interactive types
        if el_type in ("hidden", "submit", "button", "image", "reset", "search"):
            return None

        # Skip invisible elements
        try:
            if not el.is_visible():
                return None
        except Exception:
            return None

        field_type = self._map_field_type(tag, el_type)
        label      = self._find_label(el, el_id, el_name, frame)
        selector   = self._build_selector(el, el_id, el_name, label)

        try:
            current_val = el.input_value() if tag != "select" else ""
        except Exception:
            current_val = ""

        return {
            "label":           label,
            "field_type":      field_type,
            "selector":        selector,
            "required":        required,
            "maxlength":       int(maxlen) if maxlen and maxlen.isdigit() else None,
            "current_value":   current_val,
            "suggested_value": "",
            "previous_answers": [],
        }

    def _map_field_type(self, tag: str, el_type: str) -> str:
        if tag == "textarea":
            return "textarea"
        if tag == "select":
            return "select"
        return {
            "checkbox": "checkbox",
            "radio":    "radio",
            "file":     "file",
            "email":    "text",
            "tel":      "text",
            "number":   "text",
            "url":      "text",
            "date":     "text",
        }.get(el_type, "text")

    def _find_label(self, el, el_id: str, el_name: str, frame) -> str:
        # 1. <label for="id">
        if el_id:
            try:
                lbl = frame.query_selector(f'label[for="{el_id}"]')
                if lbl:
                    text = (lbl.inner_text() or "").strip()
                    if text:
                        return self._clean(text)
            except Exception:
                pass

        # 2. aria-label
        try:
            aria = el.get_attribute("aria-label")
            if aria and aria.strip():
                return self._clean(aria)
        except Exception:
            pass

        # 3. aria-labelledby
        try:
            aria_id = el.get_attribute("aria-labelledby")
            if aria_id:
                ref = frame.query_selector(f"#{aria_id}")
                if ref:
                    text = (ref.inner_text() or "").strip()
                    if text:
                        return self._clean(text)
        except Exception:
            pass

        # 4. placeholder
        try:
            ph = el.get_attribute("placeholder")
            if ph and ph.strip():
                return self._clean(ph)
        except Exception:
            pass

        # 5. name attribute
        if el_name:
            return self._clean(el_name.replace("_", " ").replace("-", " "))

        # 6. Nearest label/legend ancestor or preceding sibling via JS
        try:
            nearby = el.evaluate("""el => {
                // Wrapped in a label?
                let lbl = el.closest('label');
                if (lbl) return lbl.innerText;
                // Preceding sibling that looks like a label
                let prev = el.previousElementSibling;
                if (prev && ['LABEL','LEGEND','SPAN','DIV','P'].includes(prev.tagName))
                    return prev.innerText;
                // Parent container has a label/legend child
                let parent = el.parentElement;
                if (parent) {
                    let found = parent.querySelector(
                        'label, legend, [class*="label"], [class*="Label"]');
                    if (found && found !== el) return found.innerText;
                }
                return '';
            }""")
            if nearby and nearby.strip():
                return self._clean(nearby.strip())
        except Exception:
            pass

        return ""

    def _clean(self, text: str) -> str:
        text = re.sub(r"[*\s]+$", "", text.strip())
        text = re.sub(r"\s+", " ", text)
        return text[:200]

    def _build_selector(self, el, el_id: str, el_name: str, label: str) -> str:
        # Prefer stable ID (skip auto-generated IDs with long digit runs)
        if el_id and not re.search(r"\d{4,}", el_id):
            return f"#{el_id}"
        if el_name:
            tag = (el.evaluate("e => e.tagName") or "input").lower()
            return f'{tag}[name="{el_name}"]'
        if el_id:
            return f"#{el_id}"
        if label:
            safe = label.replace('"', '\\"')
            return f'[aria-label="{safe}"]'
        return ""
