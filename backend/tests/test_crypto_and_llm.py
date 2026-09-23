"""Secret 加密存储 + LLM 运行时配置测试。"""
from __future__ import annotations

import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite://"

from app.core.auth import hash_password
from app.core.crypto import decrypt, encrypt, is_encrypted


def test_encrypt_roundtrip():
    secret = "test-provider-secret"
    enc = encrypt(secret)
    assert enc != secret
    assert is_encrypted(enc)
    assert enc.startswith("enc:v1:")
    assert decrypt(enc) == secret


def test_encrypt_unique_ciphertext():
    assert encrypt("same") != encrypt("same")  # Fernet 随机 IV


def test_decrypt_plain_passthrough():
    # 旧明文值兼容：原样返回
    assert decrypt("legacy-plain-key") == "legacy-plain-key"
    assert not is_encrypted("legacy-plain-key")


def test_decrypt_wrong_key_returns_empty():
    enc = encrypt("secret")
    # 篡改密文
    tampered = enc[:-4] + "AAAA"
    assert decrypt(tampered) == ""


def test_provider_config_stores_encrypted(db_session):
    from app.services.provider_config import _load_stored, save_fields
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import SystemSetting

    # 独立 engine 检查落库密文（db_session 是测试共享 session，直接读它自己的库）
    save_fields(db_session, {"zhihu_api_key": "sk-plain-visible"})
    row = db_session.get(SystemSetting, "provider_config")
    stored_raw = row.value
    assert stored_raw["zhihu_api_key"].startswith("enc:v1:")
    assert "sk-plain-visible" not in str(stored_raw)
    # 解密读回
    assert _load_stored(db_session)["zhihu_api_key"] == "sk-plain-visible"


def test_llm_fields_in_config(client, db_session):
    from app.models import User as U

    if db_session.query(U).filter(U.username == "llmadmin").first() is None:
        db_session.add(U(id="usr_llmadmin", username="llmadmin", password_hash=hash_password("adminpass123"), role="admin"))
        db_session.commit()
    client.post("/api/auth/login", json={"username": "llmadmin", "password": "adminpass123"})

    # 保存 LLM 配置
    r = client.post("/api/admin/providers/config", json={"fields": {
        "llm_base_url": "https://api.deepseek.com/v1",
        "llm_api_key": "sk-llm-test-9876",
        "llm_model": "deepseek-chat",
    }})
    assert r.status_code == 200
    assert r.json()["mode"]["llm"] == "REAL"

    # LLM Key 加密落库
    from app.models import SystemSetting as SS

    row = db_session.get(SS, "provider_config")
    assert str(row.value["llm_api_key"]).startswith("enc:v1:")

    # 脱敏视图
    view = client.get("/api/admin/providers/config").json()
    llm_item = next(i for i in view["items"] if i["key"] == "llm_api_key")
    assert llm_item["value"].startswith("••••")
    assert llm_item["group"] == "llm"

    # Gateway 已热更新（同一进程）
    from app.services.llm.gateway import gateway

    assert gateway.enabled is True
    assert gateway.model == "deepseek-chat"
    assert gateway.base_url == "https://api.deepseek.com/v1"

    # 清空 → 回退 FALLBACK
    client.post("/api/admin/providers/config", json={"fields": {
        "llm_base_url": "", "llm_api_key": "", "llm_model": ""}})
    assert gateway.enabled is False
