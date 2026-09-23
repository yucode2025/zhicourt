"""Secret 加密存储：Fernet 对称加密（cryptography）。

- 密钥来源：环境变量 SECRET_ENCRYPTION_KEY（32 字节 url-safe base64）优先；
  未设置时自动生成并写入 <backend>/.secret_key 文件（已加入 .gitignore），权限仅本机。
- 密文格式：enc:v1:<urlsafe-base64>，读取时按前缀识别；
  无前缀的旧明文值保持兼容（读取时原样返回，下次保存时自动升级为密文）。
- 服务器换机器/丢密钥文件会导致已加密配置不可解密——重新在后台填写即可。
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

_PREFIX = "enc:v1:"


def _key_from_env(env_key: str) -> bytes:
    """把 SECRET_ENCRYPTION_KEY 归一为合法 Fernet 密钥。

    - 标准 32 字节 url-safe base64（44 字符）→ 原样使用
    - 其他口令 → SHA-256 派生（稳定可复现，跨重启可解密）
    """
    try:
        raw = base64.urlsafe_b64decode(env_key.encode("ascii"))
        if len(raw) == 32:
            return env_key.encode("ascii")
    except (ValueError, UnicodeEncodeError):
        pass
    return base64.urlsafe_b64encode(hashlib.sha256(env_key.encode("utf-8")).digest())


def _load_or_create_key() -> bytes:
    env_key = os.getenv("SECRET_ENCRYPTION_KEY", "").strip()
    if env_key:
        return _key_from_env(env_key)

    key_file = Path(__file__).resolve().parents[2] / ".secret_key"
    try:
        if key_file.exists():
            raw = key_file.read_text(encoding="utf-8").strip()
            if raw:
                return raw.encode()
    except OSError:
        pass
    key = base64.urlsafe_b64encode(secrets.token_bytes(32))
    try:
        key_file.write_text(key.decode("ascii"), encoding="ascii")
        try:
            key_file.chmod(0o600)
        except OSError:
            pass
        logger.info("generated new secret encryption key file")
    except OSError as exc:
        logger.warning("cannot persist encryption key file: %s — secrets will not be decryptable across restarts", exc)
    return key


def _fernet():
    from cryptography.fernet import Fernet

    global _fernet_instance
    if _fernet_instance is None:
        _fernet_instance = Fernet(_load_or_create_key())
    return _fernet_instance


_fernet_instance = None


def encrypt(plain: str) -> str:
    if not plain:
        return ""
    token = _fernet().encrypt(plain.encode("utf-8"))
    return _PREFIX + token.decode("ascii")


def decrypt(stored: str) -> str:
    if not stored:
        return ""
    if not stored.startswith(_PREFIX):
        return stored  # 旧明文值兼容：原样返回，下次保存自动升级为密文
    try:
        return _fernet().decrypt(stored[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except Exception:  # noqa: BLE001 — 密钥不匹配/数据损坏
        logger.error("secret decryption failed — 密钥可能已更换，请到管理后台重新填写")
        return ""


def is_encrypted(stored: str) -> bool:
    return stored.startswith(_PREFIX)
