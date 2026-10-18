#!/usr/bin/env python3
"""
Capability-inventory intake from the command line (the Profile page does the same in a browser).

  python jobbot_intake.py --gaps                 # open gap questions from target postings
  python jobbot_intake.py --gaps --refresh       # ...analyzing postings not read yet (model calls)
  python jobbot_intake.py --propose-from a.md b.md   # model proposes entries from notes; you confirm on /profile
  python jobbot_intake.py --list                 # the inventory, by level

Proposals never enter the inventory until confirmed on the Profile page.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import capabilities
from db import store

_BATCH_CHARS = 18000


def _batches(paths: list[Path]):
    """Group whole files into model-sized batches (a file larger than a batch goes alone, truncated)."""
    batch, size = [], 0
    for path in paths:
        text = f"=== {path.name} ===\n{path.read_text(encoding='utf-8', errors='replace')}\n"
        if batch and size + len(text) > _BATCH_CHARS:
            yield batch
            batch, size = [], 0
        batch.append((path.name, text))
        size += len(text)
    if batch:
        yield batch


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gaps", action="store_true", help="print open gap questions")
    ap.add_argument("--refresh", action="store_true", help="with --gaps: analyze unread postings first")
    ap.add_argument("--propose-from", nargs="+", type=Path, metavar="FILE",
                    help="notes files to turn into proposed entries")
    ap.add_argument("--list", action="store_true", help="print the inventory")
    args = ap.parse_args()
    if not (args.gaps or args.propose_from or args.list):
        ap.print_help()
        return

    store.init_db()
    if args.gaps:
        jds = [a["jd_text"] for a in store.list_applications(limit=500) if a["jd_text"]]
        gaps, pending = capabilities.gap_questions(jds, capabilities.load(), refresh_missing=args.refresh)
        print(f"{len(gaps)} open questions ({pending} postings not analyzed)")
        for g in gaps:
            tag = "required" if g["required"] else "preferred"
            print(f"  [{tag:9}] {g['term']:32} {g['postings']} posting(s)  — {g['snippet']}")

    if args.propose_from:
        for batch in _batches(args.propose_from):
            names = ", ".join(n for n, _ in batch)
            result = capabilities.structure_notes("".join(t for _, t in batch))
            added = capabilities.add_proposed(result["entries"], source=f"notes: {names}")
            capabilities.add_questions(result["follow_up_questions"], source=f"notes: {names}")
            print(f"{names}: {added} proposed")
            for q in result["follow_up_questions"]:
                print("   follow-up:", q)

    if args.list:
        for e in sorted(capabilities.load(), key=lambda e: (e["level"], e["name"].lower())):
            print(f"  {e['level']:12} {e['name']:32} {e['where']} {e['when']}")


if __name__ == "__main__":
    main()
