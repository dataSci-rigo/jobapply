"""
Deterministic checks on a finalized resume against the candidate's sources.

Catches what an LLM fact-checker can miss or rationalize: a skill that isn't in
the history, a term lifted from the job posting that the history never mentions
(the classic keyword-stuffing fabrication), and numbers that appear nowhere in
the sources. Each problem is a plain string the revision step can act on.
"""

import json
import re

# Capitalized words, acronyms, and tech tokens (C#, C++, 800-171, Node.js), plus
# runs of up to three such words ("Power BI", "Azure Logic Apps").
_TOKEN = r"[A-Z][A-Za-z0-9+#./-]*[A-Za-z0-9+#]|[A-Z]{2,}|C#|C\+\+"
_TERM = re.compile(rf"(?:{_TOKEN})(?:\s+(?:{_TOKEN})){{0,2}}")
_NUMBER = re.compile(r"\$\s?\d[\d,.]*\s?[MKB]?|\d[\d,.]*\s?%|\b\d+(?:\.\d+)?x\b|\b\d+\+?\s+years\b", re.I)


_COMMON = {"strong", "design", "build", "develop", "experience", "ability", "knowledge", "support",
            "familiarity", "bachelor", "master", "degree", "work", "team", "lead", "manage", "implement",
            "know", "use", "using", "must", "should", "will", "can", "and", "or", "with", "the", "a", "an",
            "in", "on", "for", "of", "to", "our", "your", "you", "we", "this", "these", "maintain", "create",
            "deliver", "ensure", "integrate", "connect", "evaluate", "proficiency", "background", "exposure",
            "hands", "plus", "preferred", "required", "including", "such", "as", "other", "all", "any",
            "build", "serve", "own", "drive", "partner", "translate", "identify", "document", "provide"}


def trim_phrase(term: str) -> str:
    """Drop ordinary leading words a capitalized run picked up ("Know OPC UA" -> "OPC UA")."""
    words = term.split()
    while len(words) > 1 and re.fullmatch(r"[A-Z][a-z]+", words[0]) and words[0].lower() in _COMMON:
        words = words[1:]
    return " ".join(words)


def _technical(term: str, jd_text: str) -> bool:
    """A product, acronym or multi-word name — not an ordinary word capitalized at a line start."""
    if " " in term or re.search(r"[0-9+#./-]", term) or re.search(r"[A-Z]", term[1:]):
        return True
    # A single capitalized word counts as a proper noun only if the posting never writes it lowercase.
    return term.lower() not in _COMMON and not re.search(rf"\b{re.escape(term.lower())}\b", jd_text)


def _corpus(*sources) -> str:
    return " ".join(json.dumps(s, ensure_ascii=False) for s in sources).lower()


def _mentions(text: str, term: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(term.lower())}(?![A-Za-z0-9])", text) is not None


def _resume_text(resume: dict) -> str:
    parts = [resume.get("summary", "")]
    for job in resume.get("experience", []) + resume.get("earlier_experience", []):
        parts += job.get("bullets", [])
    for proj in resume.get("projects", []):
        parts += [proj.get("name", ""), proj.get("description", "")]
    for values in (resume.get("skills") or {}).values():
        parts += values
    return "\n".join(parts)


def validate(resume: dict, master: dict, jd_text: str = "", extra_sources=(),
             max_years_stated: int | None = None) -> list[str]:
    """Return a list of problems (empty when the resume passes)."""
    problems = []
    corpus = _corpus(master, *extra_sources)
    text = _resume_text(resume)
    low = text.lower()

    companies = {j["company"] for j in master["experience"]}
    for job in resume.get("experience", []) + resume.get("earlier_experience", []):
        if job.get("company") not in companies:
            problems.append(f"Unknown employer {job.get('company')!r} — not in the master resume.")

    master_skills = {s.lower() for values in master.get("skills", {}).values() for s in values}
    for cat, values in (resume.get("skills") or {}).items():
        for skill in values:
            if skill.lower() not in master_skills and not _mentions(corpus, skill):
                problems.append(f"Skill {skill!r} ({cat}) isn't in the master skills or any source bullet.")

    if jd_text:
        jd_terms = {trim_phrase(m.group(0).strip(" .,;:")) for m in _TERM.finditer(jd_text)}
        for term in sorted(jd_terms, key=len, reverse=True):
            if (len(term) < 2 or not _technical(term, jd_text)
                    or not _mentions(low, term) or _mentions(corpus, term)):
                continue
            problems.append(f"Uses the job posting's term {term!r}, which appears nowhere in the "
                            "candidate's history — remove it or replace it with what he actually did.")

    years_cap = None
    for src in extra_sources:
        if isinstance(src, dict):
            years_cap = (src.get("additional") or {}).get("years_of_experience", years_cap)
    for m in _NUMBER.finditer(text):
        num = m.group(0)
        yrs = re.match(r"(\d+)\+?\s+years", num, re.I)
        if yrs and max_years_stated and int(yrs.group(1)) > max_years_stated:
            problems.append(f"States {num!r}; say '{max_years_stated}+ years' instead (presentation preference).")
            continue
        if yrs and years_cap and int(yrs.group(1)) <= int(years_cap):
            continue
        core = re.sub(r"\s+", "", num.lower()).rstrip("+")
        if core not in corpus.replace(" ", ""):
            problems.append(f"Number {num!r} doesn't appear in the sources.")
    return problems
