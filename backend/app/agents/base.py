"""Agent 公共工具：LLM JSON 调用的统一降级包装。"""
from __future__ import annotations

import math
from typing import Any

from app.core.logging import get_logger
from app.prompts import templates as T
from app.services.llm import LLMError, gateway
from app.services.llm.gateway import llm_case_requires_success, llm_case_short_circuited

logger = get_logger(__name__)


async def llm_json(
    system: str, user: str, *, max_tokens: int = 3000, raise_on_error: bool = False,
) -> dict[str, Any] | None:
    """调用 LLM；普通场景可降级，严格同步请求/案件在所有端点失败后抛出。"""
    if llm_case_short_circuited() or not gateway.enabled:
        return None
    try:
        result = await gateway.chat(system, user, json_mode=True, max_tokens=max_tokens)
        if isinstance(result.data, dict):
            return result.data
        return None
    except LLMError as exc:
        if raise_on_error or llm_case_requires_success():
            raise
        logger.warning("LLM call failed, fallback to heuristic: %s", exc)
        return None


def clean_text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def clean_list(value: Any, limit: int, length: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := clean_text(item, length))][:limit]


def clean_ids(value: Any, valid: set[str], limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item in valid))[:limit]


def clean_score(value: Any, default: float = 0.5) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return default
    return min(1.0, max(0.0, number)) if math.isfinite(number) else default


def fmt_sources_for_prompt(sources: list[tuple[int, str, str, str]]) -> str:
    lines = []
    for idx, title, summary, meta in sources:
        lines.append(f"[{idx}] {title} | {meta}\n摘要：{summary}")
    return "\n".join(lines)
