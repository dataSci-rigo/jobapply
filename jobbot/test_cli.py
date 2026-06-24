#!/usr/bin/env python3
"""
CLI smoke test — validates Phase 0-4 without a browser or API key.
Run: conda run -n jobapply python test_cli.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

def sep(title): print(f"\n{'─'*50}\n{title}\n{'─'*50}")

# ── Phase 0: data loading & DB ─────────────────────────────────────────────────
sep("Phase 0 — data loading")
import data_loader
data = data_loader.load_all()
print(f"  resume name : {data['resume']['name']}")
print(f"  prefs keys  : {list(data['prefs'].keys())[:5]}")
print(f"  star stories: {len(data['star'])}")

sep("Phase 0 — DB init")
from db import store
store.init_db()
print("  DB initialised OK")

cid = store.upsert_company("Test Corp", "test corp")
print(f"  company id  : {cid}")

aid = store.create_application(cid, "Software Engineer", jd_text="We need a Python engineer.")
print(f"  application : {aid}")

# ── Phase 4: question matching ─────────────────────────────────────────────────
sep("Phase 4 — question matching")
from matching.question_match import find_or_create_question, normalize, question_hash

q1 = "Please describe a time you improved system performance."
norm = normalize(q1)
qhash = question_hash(norm)
print(f"  normalized  : {norm}")
print(f"  hash        : {qhash[:16]}…")
qid = find_or_create_question(q1, "textarea")
print(f"  question id : {qid}")

# Save a fake answer
store.save_answer(aid, qid, "I reduced API latency by 40% using Redis caching.", source="manual")
answers = store.get_previous_answers_for_question(qid)
print(f"  prev answers: {len(answers)}")

# ── Phase 2: PDF render (no API needed) ────────────────────────────────────────
sep("Phase 2 — resume PDF render")
from resume.render import render_pdf
sample_resume = data["resume"]
pdf_path = render_pdf(sample_resume, label="test")
print(f"  PDF written : {pdf_path}")
assert pdf_path.exists(), "PDF not created!"
assert pdf_path.stat().st_size > 1000, "PDF suspiciously small"
print("  PDF size    :", pdf_path.stat().st_size, "bytes — OK")

sep("All smoke tests passed ✓")
