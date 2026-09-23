"""初始化数据库 + 演示案件种子数据。

用法：
    python scripts/init_db.py            # 执行迁移 + 种子

演示种子案件基于 demo:// 演示语料构建，UI 会明确标注“演示数据”。
真实历史 API 数据运行后可随时通过同一入口刷新。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.logging import setup_logging  # noqa: E402

setup_logging()

from app.db.session import SessionLocal, engine  # noqa: E402
from app.models import Base  # noqa: E402


def main() -> None:
    Base.metadata.create_all(bind=engine)
    print("[init_db] tables created")

    from sqlalchemy import select

    db = SessionLocal()
    try:
        from app.models import Case

        demo = db.execute(select(Case).where(Case.is_demo.is_(True))).scalars().first()
        if demo:
            print(f"[init_db] demo case exists: {demo.id}")
            return
    finally:
        db.close()

    # 通过完整工作流构建一个演示案件（Mock Provider + 启发式引擎）
    from app.models import Case, gen_id

    db = SessionLocal()
    try:
        case = Case(
            id=gen_id("case"),
            title="AI 会淘汰程序员吗",
            original_question="AI 会不会淘汰程序员？",
            proposition="未来几年 AI 是否会显著减少初级软件开发岗位需求",
            status="queued",
            is_demo=True,
            # 演示种子案件保持公开，供首页/历史列表展示；用户自建案件仍默认私有
            is_public=True,
            public_id=uuid.uuid4().hex[:20],
        )
        db.add(case)
        db.commit()
        case_id = case.id
    finally:
        db.close()

    from app.services.case_lifecycle import claim_lease, lease_owner_id
    from app.workflows.trial import TrialWorkflow as WF
    db = SessionLocal()
    try:
        owner = lease_owner_id()
        claimed = claim_lease(db, case_id, owner)
        if claimed is None:
            raise RuntimeError(f"[init_db] could not claim demo case: {case_id}")
    finally:
        db.close()
    token, generation, attempt = claimed
    WF(case_id, generation=generation, lease_token=token, owner=owner, attempt=attempt).run()
    db = SessionLocal()
    try:
        case = db.get(Case, case_id)
        if case is None or case.status != "verdict_ready":
            raise RuntimeError(f"[init_db] demo case did not finish: {case_id}")
    finally:
        db.close()
    print(f"[init_db] demo case ready: {case_id}")


if __name__ == "__main__":
    main()
