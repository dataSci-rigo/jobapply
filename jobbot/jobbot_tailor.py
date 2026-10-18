#!/usr/bin/env python3
"""
Tailor, check and render a resume for one job — the same pipeline as the Review
screen's "Generate resume", from the command line.

  python jobbot_tailor.py --app 12                 # job description from application #12
  python jobbot_tailor.py --jd-file job.txt --label "Acme – Engineer"

Prints every fact-check round, renders the PDF, and (with --app) saves it as a
resume version linked to that application. Costs real API calls (Opus).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from db import store
from generation.resume_tailor import tailor_resume_with_report
from resume.render import render_pdf


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--app", type=int, help="application id whose stored job description to use")
    src.add_argument("--jd-file", type=Path, help="text file holding a job description")
    ap.add_argument("--label", default="", help="PDF label (defaults to company – title)")
    ap.add_argument("--json-out", type=Path, help="also write the resume JSON + report here")
    args = ap.parse_args()

    store.init_db()
    if args.app:
        application = store.get_application(args.app)
        if not application:
            sys.exit(f"No application #{args.app}")
        jd_text = application["jd_text"] or ""
        label = args.label or f"{application['company_name']} – {application['job_title']}"
    else:
        jd_text = args.jd_file.read_text()
        label = args.label or args.jd_file.stem

    resume, report = tailor_resume_with_report(jd_text)
    for i, rnd in enumerate(report["rounds"]):
        print(f"\n── check round {i}: {len(rnd['automated'])} automated, "
              f"{len(rnd['violations'])} violations, {len(rnd['missed'])} missed")
        for a in rnd["automated"]:
            print("   auto:", a)
        for v in rnd["violations"]:
            print(f"   violation @ {v['where']}: {v['claim']!r} — {v['problem']} → {v['fix']}")
        for m in rnd["missed"]:
            print(f"   missed: {m['source_fact']} → {m['where']}")
    if "final_fixes" in report:
        ff = report["final_fixes"]
        print(f"\n── final fixes applied by code: {ff['applied']}, not found: {len(ff['unapplied'])}")
        for v in ff["unapplied"]:
            print(f"   NOT APPLIED @ {v['where']}: {v['claim']!r} → {v['fix']}")
        for a in ff["automated_after"]:
            print("   auto after fixes:", a)
    print("\nclean:", report["clean"])

    pdf = render_pdf(resume, label=label)
    print("PDF:", pdf)
    if args.app:
        rv = store.save_resume_version(resume, str(pdf), label, application_id=args.app)
        store.link_resume_to_application(args.app, rv)
        print("resume_version:", rv)
    if args.json_out:
        args.json_out.write_text(json.dumps({"resume": resume, "report": report}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
