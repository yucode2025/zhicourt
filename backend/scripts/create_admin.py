"""从环境变量创建首个管理员（幂等，生产部署第一步后执行一次）。

用法：
    ADMIN_USERNAME=admin ADMIN_PASSWORD=强密码 python scripts/create_admin.py
创建后建议从 .env 移除 ADMIN_PASSWORD 并修改密码。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.auth import seed_admin_from_env  # noqa: E402
from app.core.logging import setup_logging  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402

setup_logging()

if __name__ == "__main__":
    db = SessionLocal()
    try:
        print("[create_admin]", seed_admin_from_env(db))
    finally:
        db.close()
