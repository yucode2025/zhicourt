"""Evidence Agent：从来源中提取结构化证据。"""
from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.agents.base import clean_list, clean_text, llm_json
from app.models import Evidence, Source, gen_id
from app.prompts import templates as T

VALID_TYPES = {"fact", "opinion", "data", "case", "prediction", "assumption", "limitation"}
VALID_STANCES = {"pro", "con", "neutral"}


def semantic_excerpt(text: str, limit: int) -> str:
    """尽量在中文/英文句末截断，避免 UI 出现半句话；原文仍由 Source 保留。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    candidate = text[:limit]
    floor = int(limit * 0.58)
    cut = max(candidate.rfind(mark) for mark in ("。", "！", "？", ". ", "! ", "? ", "；", "; "))
    if cut >= floor:
        return candidate[: cut + 1].rstrip()
    return candidate.rstrip() + "…"


def _guess_stance(text: str) -> str:
    pro_words = ["支持", "成立", "符合", "降低", "改善", "收缩", "下滑", "减少", "淘汰", "替代", "威胁", "下降", "削减"]
    con_words = ["反对", "不成立", "不符合", "取消", "扩大", "保留", "增长", "机会", "不会", "难以", "反而", "仍会"]
    p = sum(text.count(w) for w in pro_words)
    c = sum(text.count(w) for w in con_words)
    if p > c:
        return "pro"
    if c > p:
        return "con"
    return "neutral"


def _guess_type(text: str) -> str:
    if any(w in text for w in ["报告", "统计", "%", "％", "数据", "调研", "办法", "条例", "通知", "规定"]):
        return "data"
    if any(w in text for w in ["预测", "将会", "预计", "未来几年", "趋势"]):
        return "prediction"
    if any(w in text for w in ["身边", "我所在", "我们公司", "见过", "案例", "当事人"]):
        return "case"
    if any(w in text for w in ["口径", "局限", "样本", "不确定"]):
        return "limitation"
    if any(w in text for w in ["历史", "表明", "事实上", "成立于", "出现于", "实施", "发布"]):
        return "fact"
    return "opinion"


def _is_demo_source(source: Source) -> bool:
    return bool(
        getattr(source, "is_demo", False)
        or getattr(source, "is_demo_source", False)
        or urlparse(source.url or "").scheme == "demo"
    )


def _demo_hints(source: Source) -> tuple[str | None, str | None]:
    """读取归一化结果随演示 Source 传下来的受限分类提示。"""
    if not _is_demo_source(source):
        return None, None
    stance = getattr(source, "stance_hint", None)
    evidence_type = getattr(source, "evidence_type_hint", None)
    if stance in VALID_STANCES and evidence_type in VALID_TYPES:
        return stance, evidence_type

    parsed = urlparse(source.url or "")
    if parsed.scheme == "demo":
        params = parse_qs(parsed.query)
        stance = stance or (params.get("stance_hint") or [None])[0]
        evidence_type = evidence_type or (params.get("evidence_type_hint") or [None])[0]
    return (
        stance if stance in VALID_STANCES else None,
        evidence_type if evidence_type in VALID_TYPES else None,
    )


def _source_classification(source: Source) -> tuple[str, str]:
    """演示来源服从显式 hint；真实来源才执行文本启发式分类。"""
    stance_hint, type_hint = _demo_hints(source)
    if _is_demo_source(source):
        # 旧缓存可能缺少 hint；此时也只看正文，不让标题中的“演示数据”触发 data。
        text = source.summary or ""
    else:
        text = f"{source.title} {source.summary or source.title}"
    return stance_hint or _guess_stance(text), type_hint or _guess_type(text)


def heuristic_extract(
    proposition: str,
    sources: list[Source],
    *,
    start_index: int = 0,
    limitation: str = "来源真实；本条由来源摘要规则提取，未经 LLM 全文精读，请点击来源核验",
) -> list[dict[str, Any]]:
    """降级提取：只使用真实来源摘要，不生成新事实。"""
    out: list[dict[str, Any]] = []
    for local_i, source in enumerate(sources):
        text = source.summary or source.title
        if not text:
            continue
        stance, evidence_type = _source_classification(source)
        item_limitation = limitation
        if _is_demo_source(source):
            item_limitation = "演示来源；仅用于展示论证结构，不构成现实事实，请替换为可追溯来源后核验"
        out.append(
            {
                "source_index": start_index + local_i,
                "claim": semantic_excerpt(text, 260),
                "stance": stance,
                "evidence_type": evidence_type,
                "summary": semantic_excerpt(text, 1200),
                "quoted_fragment": None,
                "limitations": [item_limitation],
            }
        )
    return out


def _clean_llm_items(data: dict[str, Any] | None, batch: list[Source], start_index: int) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or not isinstance(data.get("evidence"), list):
        return []
    cleaned: list[dict[str, Any]] = []
    for raw in data["evidence"]:
        if not isinstance(raw, dict):
            continue
        local_idx = raw.get("source_index")
        if isinstance(local_idx, bool) or not isinstance(local_idx, int) or not (0 <= local_idx < len(batch)):
            continue
        claim = clean_text(raw.get("claim"), 500)
        if not claim:
            continue
        item = {"claim": claim, "summary": clean_text(raw.get("summary"), 1500),
                "quoted_fragment": clean_text(raw.get("quoted_fragment"), 500) or None}
        item["source_index"] = start_index + local_idx
        evidence_type = raw.get("evidence_type")
        stance = raw.get("stance")
        hint_stance, hint_type = _demo_hints(batch[local_idx])
        # 演示模板的立场/类型是生成时的结构化元数据，不能让标题标记或 LLM 猜测覆盖。
        item["evidence_type"] = hint_type or (evidence_type if evidence_type in VALID_TYPES else "opinion")
        item["stance"] = hint_stance or (stance if stance in VALID_STANCES else "neutral")
        raw_limitations = raw.get("limitations", [])
        if not isinstance(raw_limitations, list):
            raw_limitations = []
        limitations = clean_list(raw_limitations, 5, 200)
        if hint_stance or hint_type:
            demo_limitation = "演示来源；仅用于展示论证结构，不构成现实事实"
            if demo_limitation not in limitations:
                limitations.insert(0, demo_limitation)
        item["limitations"] = limitations[:5]
        cleaned.append(item)
    return cleaned


async def _llm_extract_batch(proposition: str, batch: list[Source], start_index: int) -> list[dict[str, Any]]:
    def source_line(i: int, source: Source) -> str:
        stance_hint, type_hint = _demo_hints(source)
        demo_note = ""
        if _is_demo_source(source):
            demo_note = f"，演示来源，stance_hint={stance_hint or 'neutral'}，evidence_type_hint={type_hint or 'opinion'}"
        return (
            f"[{i}] {source.title}（{source.origin}/{source.kind}，{source.published_at or '时间未知'}{demo_note}）："
            f"{source.summary[:420]}"
        )

    src_lines = "\n".join(source_line(i, source) for i, source in enumerate(batch))
    data = await llm_json(
        T.EVIDENCE_SYSTEM,
        T.fill(
            T.EVIDENCE_USER,
            proposition=proposition,
            sources=src_lines,
            max_evidence=min(8, max(3, len(batch) * 2)),
        ),
        max_tokens=2200,
    )
    return _clean_llm_items(data, batch, start_index)


async def run_evidence_agent(
    proposition: str, sources: list[Source], *, mode: str
) -> tuple[list[dict[str, Any]], str]:
    """返回 (证据项, agent_mode)。LLM 模式按 6 条来源分批，部分失败时输出 mixed。"""
    if mode != "llm":
        return heuristic_extract(proposition, sources), "heuristic"

    batch_size = 6
    batches = [(start, sources[start : start + batch_size]) for start in range(0, len(sources), batch_size)]
    results = await asyncio.gather(
        *(_llm_extract_batch(proposition, batch, start) for start, batch in batches),
        return_exceptions=True,
    )

    items: list[dict[str, Any]] = []
    llm_batches = 0
    fallback_batches = 0
    for (start, batch), result in zip(batches, results):
        if isinstance(result, list) and result:
            items.extend(result)
            llm_batches += 1
        else:
            items.extend(
                heuristic_extract(
                    proposition,
                    batch,
                    start_index=start,
                    limitation="来源真实；本批 LLM 精读请求超时或返回结构异常，已从来源摘要规则提取，请点击来源核验",
                )
            )
            fallback_batches += 1

    if llm_batches and fallback_batches:
        agent_mode = "mixed"
    elif llm_batches:
        agent_mode = "llm"
    else:
        agent_mode = "heuristic"
    return items[: len(sources) * 10], agent_mode


def build_evidence_rows(case_id: str, sources: list[Source], items: list[dict[str, Any]]) -> list[Evidence]:
    rows: list[Evidence] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        idx = item.get("source_index")
        if isinstance(idx, bool) or not isinstance(idx, int) or not (0 <= idx < len(sources)):
            continue
        source = sources[idx]
        limitations = item.get("limitations", [])
        if not isinstance(limitations, list):
            limitations = []
        if _is_demo_source(source):
            stance, evidence_type = _source_classification(source)
        else:
            stance = item.get("stance") if isinstance(item.get("stance"), str) and item["stance"] in VALID_STANCES else "neutral"
            evidence_type = item.get("evidence_type") if isinstance(item.get("evidence_type"), str) and item["evidence_type"] in VALID_TYPES else "opinion"
        row = Evidence(
            id=gen_id("ev"),
            case_id=case_id,
            source_id=source.id,
            claim=clean_text(item.get("claim"), 500) or source.title[:200],
            stance=stance,
            evidence_type=evidence_type,
            summary=clean_text(item.get("summary"), 1500),
            quoted_fragment=clean_text(item.get("quoted_fragment"), 500) or None,
            limitations=clean_list(limitations, 5, 200),
        )
        rows.append(row)
    return rows
