"""
Tailor the master resume to a job description: draft -> finalize -> check -> revise.

1. draft     GENERATION_MODEL writes the content (summary, bullets, projects, skills)
             as schema-constrained JSON.
2. finalize  presentation.finalize() copies header, employers, titles, locations,
             dates and degrees from master_resume.json and applies the date rules.
3. check     validate.validate() runs deterministic checks, then REVIEW_MODEL
             fact-checks every claim against the sources and lists strong facts
             the draft missed.
4. revise    GENERATION_MODEL applies the fixes (automated and fact-check) and
             adds the missed facts; back to step 2, at most MAX_REVISIONS times.
             The last revision only fixes, so it can't introduce new claims that
             no check would see.
"""

import copy
import json
import re
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
import config
import data_loader
from generation.client import generate_json
from generation.presentation import finalize, fit_to_pages, presentation_prefs
from generation.validate import validate
from capabilities import claimable
from resume.render import count_pages

_PROMPTS = Path(__file__).parent / "prompts"
_TEMPLATE = (_PROMPTS / "resume_tailor.txt").read_text()
_CRITIC   = (_PROMPTS / "resume_critic.txt").read_text()
_REVISE   = (_PROMPTS / "resume_revise.txt").read_text()

_STRINGS = {"type": "array", "items": {"type": "string"}}
_ROLE = {
    "type": "object",
    "properties": {"company": {"type": "string"}, "bullets": _STRINGS},
    "required": ["company", "bullets"],
    "additionalProperties": False,
}
RESUME_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "experience": {"type": "array", "items": _ROLE},
        "earlier_experience": {"type": "array", "items": _ROLE},
        "projects": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
            "required": ["name", "description"],
            "additionalProperties": False,
        }},
        "skills": {
            "type": "object",
            "properties": {k: _STRINGS for k in ("languages", "frameworks", "ml", "tools", "other")},
            "required": ["languages", "frameworks", "ml", "tools", "other"],
            "additionalProperties": False,
        },
        "education": {"type": "array", "items": {
            "type": "object",
            "properties": {"institution": {"type": "string"}, "degree": {"type": "string"}},
            "required": ["institution", "degree"],
            "additionalProperties": False,
        }},
    },
    "required": ["summary", "experience", "earlier_experience", "projects", "skills", "education"],
    "additionalProperties": False,
}
CRITIC_SCHEMA = {
    "type": "object",
    "properties": {
        "violations": {"type": "array", "items": {
            "type": "object",
            "properties": {k: {"type": "string"} for k in ("where", "claim", "problem", "fix")},
            "required": ["where", "claim", "problem", "fix"],
            "additionalProperties": False,
        }},
        "missed": {"type": "array", "items": {
            "type": "object",
            "properties": {k: {"type": "string"} for k in ("source_fact", "where")},
            "required": ["source_fact", "where"],
            "additionalProperties": False,
        }},
    },
    "required": ["violations", "missed"],
    "additionalProperties": False,
}


def _content(resume: dict) -> dict:
    """The part of a finalized resume the model owns, plus the headings it renders under."""
    keys = list(RESUME_SCHEMA["properties"]) + ["projects_heading", "earlier_experience_heading"]
    return {k: resume.get(k) for k in keys}


def _automated(resume: dict, master: dict, prefs: dict, star: list, jd: str) -> list[str]:
    p = presentation_prefs(prefs)
    caps = claimable(data_loader.get_capabilities())
    found = validate(resume, master, jd, extra_sources=(prefs, star, caps),
                     max_years_stated=p["max_years_stated"])
    pages = count_pages(resume)
    if pages > p["max_pages"]:
        found.append(f"Renders to {pages} pages; cut to {p['max_pages']} by dropping the weakest bullets, "
                     "projects and skills (keep every role).")
    return found


def _tidy(text: str) -> str:
    text = re.sub(r"\s+([,.;:])", r"\1", re.sub(r"\s{2,}", " ", text)).strip()
    return re.sub(r"^[,;:]\s*", "", text)


def _apply_fixes(resume: dict, violations: list[dict]) -> tuple[dict, list[dict]]:
    """
    Apply the fact-checker's fixes as literal find-and-replace — no model call, so
    nothing new can slip in after the last check. Returns (resume, fixes not found).
    """
    r = copy.deepcopy(resume)
    unapplied = []
    for v in violations:
        claim = v["claim"].strip().strip("'\"")
        fix = "" if v["fix"].strip().lower() == "delete" else v["fix"].strip()
        if claim and claim in r.get("summary", ""):
            r["summary"] = _tidy(r["summary"].replace(claim, fix, 1))
            continue
        places = [(job, "bullets") for sec in ("experience", "earlier_experience") for job in r.get(sec, [])]
        hit = False
        for job, key in places:
            for k, bullet in enumerate(job[key]):
                if claim and claim in bullet:
                    job[key][k] = _tidy(bullet.replace(claim, fix, 1))
                    hit = True
                    break
            if hit:
                job[key] = [b for b in job[key] if b]
                break
        if not hit:
            for proj in r.get("projects", []):
                if claim and claim in proj.get("description", ""):
                    proj["description"] = _tidy(proj["description"].replace(claim, fix, 1))
                    hit = True
                    break
        if not hit:
            unapplied.append(v)
    return r, unapplied


def tailor_resume_with_report(jd_text: str) -> tuple[dict, dict]:
    """Return (render-ready resume, report of every check round)."""
    master = data_loader.get_resume()
    prefs = data_loader.load_all()["prefs"]
    star = data_loader.load_all()["star"]
    jd = jd_text[:6000]
    cutoff = presentation_prefs(prefs)["earlier_experience_ends_before"]

    draft = generate_json(_TEMPLATE.format(jd_text=jd, cutoff_year=cutoff), RESUME_SCHEMA)
    report = {"rounds": []}
    max_pages = presentation_prefs(prefs)["max_pages"]
    extra_skills = {e["name"] for e in claimable(data_loader.get_capabilities())}
    for round_no in range(config.MAX_REVISIONS + 1):
        resume, cuts = fit_to_pages(finalize(draft, master, prefs, extra_skills=extra_skills),
                                    max_pages, count_pages)
        auto = _automated(resume, master, prefs, star, jd)
        review = generate_json(
            _CRITIC.format(auto_findings="\n".join(f"- {a}" for a in auto) or "(none)",
                           jd_text=jd, resume_json=json.dumps(_content(resume), indent=1)),
            CRITIC_SCHEMA, model=config.REVIEW_MODEL, effort=config.REVIEW_EFFORT,
            max_tokens=32000,
        )
        report["rounds"].append({"automated": auto, "trimmed": cuts, **review})
        last = round_no == config.MAX_REVISIONS
        if last and review["violations"]:
            resume, unapplied = _apply_fixes(resume, review["violations"])
            report["final_fixes"] = {
                "applied": len(review["violations"]) - len(unapplied),
                "unapplied": unapplied,
                "automated_after": _automated(resume, master, prefs, star, jd),
            }
        if last or not (auto or review["violations"] or review["missed"]):
            break
        final_revision = round_no == config.MAX_REVISIONS - 1
        draft = generate_json(
            _REVISE.format(automated="\n".join(f"- {a}" for a in auto) or "(none)",
                           violations=json.dumps(review["violations"], indent=1),
                           missed="(none — this is the final revision: fix only)" if final_revision
                                  else json.dumps(review["missed"], indent=1),
                           jd_text=jd, resume_json=json.dumps(_content(resume), indent=1)),
            RESUME_SCHEMA,
        )
    final_round = report["rounds"][-1]
    if "final_fixes" in report:
        ff = report["final_fixes"]
        report["clean"] = not ff["unapplied"] and not ff["automated_after"]
    else:
        report["clean"] = not final_round["violations"] and not final_round["automated"]
    return resume, report


def tailor_resume(jd_text: str) -> dict:
    """Return a render-ready tailored resume (the check report is discarded)."""
    resume, _ = tailor_resume_with_report(jd_text)
    return resume
