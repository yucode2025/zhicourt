"""日志工具：统一格式，绝不输出 API Key / 密码。"""
from __future__ import annotations

import logging
import re

# 值形态：`name=value` / `name: value` / JSON 的 `"name": "value"`（引号可出现在两侧）
_KV_PATTERN = re.compile(
    r"(?i)((?:zhicourt_(?:session|sid|oauth_state)|oauth[_-]?state|authorization[_-]?code"
    r"|app[_-]?key|session[_-]?id|access[_-]?token|refresh[_-]?token"
    r"|api[_-]?key|apikey|access[_-]?secret|client[_-]?secret|secret|password|passwd|token)"
    r"(?:[\"']?\s*[:=]\s*[\"']?))([^\s\"',}\]]+)"
)
# Bearer 形态：`Authorization: Bearer xxx` 与字典形式 `"Authorization": "Bearer xxx"`
_BEARER_PATTERN = re.compile(r"(?i)((?:authorization[\"']?\s*:\s*[\"']?)bearer\s+)(\S+)")
# Cookie 头：整行只含凭据，值整体打码
_COOKIE_PATTERN = re.compile(r"(?i)((?:set-)?cookie\s*:\s*)([^\r\n]+)")

# Bearer 必须先于 KV 处理，避免 KV 先吞掉 "Bearer" 一词导致令牌残留
_REDACT_PATTERNS = [_BEARER_PATTERN, _KV_PATTERN, _COOKIE_PATTERN]


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pat in _REDACT_PATTERNS:
            msg = pat.sub(r"\1***", msg)
        record.msg = msg
        record.args = ()
        return True


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.addHandler(_shared_handler())
        logger.setLevel(logging.INFO)
    return logger


_handler: logging.Handler | None = None


def _shared_handler() -> logging.Handler:
    global _handler
    if _handler is None:
        _handler = logging.StreamHandler()
        _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        _handler.addFilter(RedactingFilter())
    return _handler


def setup_logging(level: int = logging.INFO) -> None:
    handler = _shared_handler()
    root = logging.getLogger()
    root.setLevel(level)
    if handler not in root.handlers:
        root.addHandler(handler)
