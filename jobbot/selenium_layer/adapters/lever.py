"""
Lever adapter (Patchright).
Lever applications are single-page forms at apply.lever.co.
No iframe; fields have <label> elements and data-qa attributes.
"""


LEVER_HOST = "apply.lever.co"


def is_lever(page) -> bool:
    return LEVER_HOST in page.url


def get_lever_fields(page) -> list[dict]:
    """Return Lever-specific field descriptors using data-qa attributes."""
    fields = []
    try:
        cards = page.query_selector_all("[data-qa]")
        for card in cards:
            qa = card.get_attribute("data-qa") or ""
            if not qa:
                continue
            label = ""
            try:
                lbl_el = card.query_selector("label, .application-label")
                if lbl_el:
                    label = (lbl_el.inner_text() or "").strip()
            except Exception:
                pass
            if not label:
                label = qa.replace("-", " ").title()
            fields.append({"label": label, "data_qa": qa})
    except Exception:
        pass
    return fields
