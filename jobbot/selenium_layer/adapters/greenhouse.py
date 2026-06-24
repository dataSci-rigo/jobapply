"""
Greenhouse adapter (Patchright).
Greenhouse embeds the application form in an iframe from boards.greenhouse.io.
The generic extractor handles iframes natively; this adapter adds helpers for
Greenhouse-specific label resolution inside that frame.
"""

from typing import Optional


GREENHOUSE_FRAME_URL = "greenhouse.io"


def get_greenhouse_frame(page) -> Optional[object]:
    """Return the Playwright Frame object for the Greenhouse embed, or None."""
    for frame in page.frames:
        if GREENHOUSE_FRAME_URL in frame.url:
            return frame
    return None


def resolve_label_greenhouse(frame, el) -> str:
    """
    Greenhouse wraps questions in <div class='field'> or <div class='question'>.
    Extract the label text from the nearest wrapper.
    """
    try:
        text = el.evaluate("""el => {
            const field = el.closest('.field, .question');
            if (!field) return '';
            const lbl = field.querySelector('label, .field-label, .question-label');
            return lbl ? lbl.innerText.trim() : '';
        }""")
        return (text or "").strip()[:200]
    except Exception:
        return ""
