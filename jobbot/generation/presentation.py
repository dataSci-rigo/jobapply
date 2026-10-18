"""
Deterministic finishing for a tailored resume.

The model chooses and words the content; this module owns the facts it must not
touch. Header, company, title, location, dates and degrees are copied from
master_resume.json, and preferences.json "presentation" decides how dates show:
roles that ended before a cutoff year move to an undated "earlier experience"
section, and graduation years can be hidden, so the resume reads on capability
rather than age without changing a single fact.
"""

import copy
import json
import re

PRESENTATION_DEFAULTS = {
    "earlier_experience_ends_before": 2010,
    "earlier_experience_heading": "Additional Experience",
    "show_graduation_years": False,
    "show_gpa": False,
    "show_languages": True,
    "projects_heading": "Personal Projects",
    "max_years_stated": 15,      # "15+ years" rather than the exact count
    "max_pages": 2,
}

_HEADER_KEYS = ("name", "email", "phone", "location", "linkedin", "github", "portfolio")


def presentation_prefs(prefs: dict) -> dict:
    return {**PRESENTATION_DEFAULTS, **(prefs or {}).get("presentation", {})}


def _end_year(job: dict) -> int:
    end = str(job.get("end", ""))
    return 9999 if end.lower() == "present" else int(end[:4])


def is_earlier(master_job: dict, prefs: dict) -> bool:
    """True when a master role ended before the presentation cutoff year."""
    return _end_year(master_job) < presentation_prefs(prefs)["earlier_experience_ends_before"]


def _supported_skills(skills: dict, master: dict, extra: set = frozenset()) -> dict:
    """Keep only skills the master lists, names in a bullet or project, or claimable
    inventory entries (plain list items, safe to drop)."""
    listed = {s.lower() for values in master.get("skills", {}).values() for s in values}
    listed |= {s.lower() for s in extra}
    corpus = json.dumps(master, ensure_ascii=False).lower()
    def ok(skill):
        return skill.lower() in listed or re.search(
            rf"(?<![a-z0-9]){re.escape(skill.lower())}(?![a-z0-9])", corpus) is not None
    return {cat: [s for s in values if ok(s)] for cat, values in (skills or {}).items()}


def finalize(tailored: dict, master: dict, prefs: dict, extra_skills: set = frozenset()) -> dict:
    """Return a render-ready resume: facts from master, content from the model."""
    p = presentation_prefs(prefs)
    r = copy.deepcopy(tailored)
    r.pop("_provenance", None)

    for key in _HEADER_KEYS:
        r[key] = master.get(key, "")

    by_company = {j["company"]: j for j in master["experience"]}
    chosen = {j.get("company"): j for j in r.get("experience", []) + r.get("earlier_experience", [])}

    # Every recent role stays (no unexplained gaps); a recent role the model dropped
    # comes back with its master bullet. Earlier roles appear only if the model chose them.
    current, earlier = [], []
    for src in sorted(master["experience"], key=lambda j: j["start"], reverse=True):
        job = chosen.get(src["company"])
        if job is None:
            if is_earlier(src, prefs):
                continue
            job = {"bullets": src["bullets"][:1]}
        job = {**job, "company": src["company"], "title": src["title"], "location": src["location"]}
        if is_earlier(src, prefs):
            job["start"] = job["end"] = ""
            earlier.append(job)
        else:
            job["start"] = src["start"][:4]
            job["end"] = "Present" if src["end"].lower() == "present" else src["end"][:4]
            current.append(job)
    # Companies the model invented are kept so validate() can flag them, not silently dropped.
    current += [j for c, j in chosen.items() if c not in by_company]
    r["experience"] = current
    r["earlier_experience"] = earlier
    r["earlier_experience_heading"] = p["earlier_experience_heading"]

    by_inst = {e["institution"]: e for e in master.get("education", [])}
    education = []
    for e in r.get("education", []) or master.get("education", []):
        src = by_inst.get(e.get("institution"))
        if src is None:
            education.append(e)
            continue
        edu = {"institution": src["institution"], "degree": src["degree"],
               "location": src.get("location", ""),   # ATS parsers (Indeed) require a school city/state
               "graduated": src["graduated"][:4] if p["show_graduation_years"] else ""}
        if p["show_gpa"] and src.get("gpa"):
            edu["gpa"] = src["gpa"]
        education.append(edu)
    r["education"] = education

    r["languages"] = master.get("languages", []) if p["show_languages"] else []
    r["skills"] = _supported_skills(r.get("skills"), master, extra_skills)
    r["projects_heading"] = p["projects_heading"]
    return r


def _cap_skills(n):
    def step(r):
        r["skills"] = {cat: values[:n] for cat, values in (r.get("skills") or {}).items()}
    return f"skills: top {n} per category", step


def _cap_projects(n):
    def step(r):
        r["projects"] = (r.get("projects") or [])[:n]
    return f"projects: first {n}", step


def _cap_bullets(n, from_role=0, section="experience"):
    def step(r):
        for i, job in enumerate(r.get(section) or []):
            if i >= from_role:
                job["bullets"] = job.get("bullets", [])[:n]
    return f"{section}: {n} bullet(s) from role {from_role + 1} on", step


# Lowest-value content goes first: the tail of each skills list (the model orders
# them by relevance), extra projects, then bullets on older roles.
_TRIM_STEPS = [
    _cap_skills(8), _cap_projects(4), _cap_bullets(1, section="earlier_experience"),
    _cap_skills(6), _cap_bullets(1, from_role=4), _cap_projects(3), _cap_bullets(2, from_role=3),
    _cap_skills(5), _cap_bullets(3, from_role=1), _cap_projects(2), _cap_bullets(2, from_role=1),
    _cap_skills(4), _cap_bullets(3), _cap_projects(1),
]


def fit_to_pages(resume: dict, max_pages: int, count_pages) -> tuple[dict, list[str]]:
    """
    Trim until the resume renders within max_pages; return (resume, what was cut).
    Only ever removes content, so it can't introduce a claim no check has seen.
    """
    r = copy.deepcopy(resume)
    cuts = []
    for label, step in _TRIM_STEPS:
        if count_pages(r) <= max_pages:
            break
        before = json.dumps(r, sort_keys=True)
        step(r)
        if json.dumps(r, sort_keys=True) != before:
            cuts.append(label)
    return r, cuts
