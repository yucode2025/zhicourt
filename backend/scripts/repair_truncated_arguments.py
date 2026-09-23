"""修复早期启发式模式按 200 字硬截断的论据。

仅处理标题为“正方/反方核心论证”的启发式论据；使用它已引用的 Evidence.summary 回填，
不引入任何新事实或新来源。幂等，可重复执行。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.agents.evidence_agent import semantic_excerpt
from app.db.session import SessionLocal
from app.models import Argument, Evidence


def main() -> None:
    db = SessionLocal()
    repaired = 0
    try:
        arguments = db.execute(select(Argument)).scalars().all()
        for argument in arguments:
            if not argument.title.startswith(("正方核心论证", "反方核心论证")):
                continue
            if len(argument.body or "") > 230 or not argument.evidence_ids:
                continue
            evidence = db.get(Evidence, argument.evidence_ids[0])
            if evidence is None:
                continue
            full = (evidence.summary or evidence.claim or "").strip()
            if len(full) <= len(argument.body or "") + 30:
                continue
            argument.body = semantic_excerpt(full, 900)
            repaired += 1
        db.commit()
        print(f"[repair_truncated_arguments] repaired={repaired}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
