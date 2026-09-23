"""Dry-run by default: restore legacy prefix-truncated titles from original questions.

Usage: python scripts/backfill_case_titles.py [--apply]
Only titles matching an original-question prefix of 30 or 60 characters are eligible.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models import Case


def candidate_title(case: Case) -> str | None:
    question = (case.original_question or "").strip()
    if len(question) <= len(case.title) or len(case.title) not in (30, 60):
        return None
    if case.title != question[:len(case.title)]:
        return None
    return question[:200]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="commit eligible changes (default: dry-run)")
    args = parser.parse_args()
    with SessionLocal() as db:
        count = 0
        for case in db.execute(select(Case)).scalars():
            title = candidate_title(case)
            if title is None:
                continue
            print(f"{case.id}: {case.title!r} -> {title!r}")
            case.title = title
            count += 1
        if args.apply:
            db.commit()
        else:
            db.rollback()
        print(f"eligible={count}, applied={args.apply}")


if __name__ == "__main__":
    main()
