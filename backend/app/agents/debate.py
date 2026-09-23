"""Prosecutor / Defense Agent 共享实现（Steelman 辩论构建）。"""
from __future__ import annotations

from typing import Any

from app.agents.base import clean_ids, clean_score, clean_text, llm_json
from app.models import Argument, Claim, Evidence, gen_id
from app.prompts import templates as T


def _fmt_evidence(evidence: list[Evidence], stance_filter: set[str]) -> str:
    lines = []
    for ev in evidence:
        if ev.stance not in stance_filter:
            continue
        lines.append(f"{ev.id}: {ev.claim[:150]} [{ev.evidence_type}/{ev.stance}]")
    return "\n".join(lines) or "（无）"


def _fmt_evidence_all(evidence: list[Evidence]) -> str:
    return "\n".join(f"{ev.id}: {ev.claim[:150]} [{ev.evidence_type}/{ev.stance}]" for ev in evidence) or "（无）"


def heuristic_arguments(
    side: str, evidence: list[Evidence]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """启发式辩论：按证据强度直接构建论点。"""
    if side == "prosecution":
        pool = [e for e in evidence if e.stance == "pro"]
        title, body = "正方核心论证", "基于现有证据中支持命题的部分，按证据可信度组织论证。"
    else:
        pool = [e for e in evidence if e.stance == "con"]
        title, body = "反方核心论证", "基于现有证据中反对命题的部分与反例，组织最强反对论证。"
    pool = sorted(pool, key=lambda e: e.strength, reverse=True)
    from app.agents.evidence_agent import semantic_excerpt

    claims = [{"text": semantic_excerpt(e.claim, 220), "evidence_ids": [e.id]} for e in pool[:3]]
    args = []
    for i, e in enumerate(pool[:3]):
        args.append(
            {
                "title": f"{title}（{i + 1}）",
                "body": semantic_excerpt(e.summary or e.claim, 900),
                "evidence_ids": [e.id],
                "strength": e.strength,
            }
        )
    if not args:
        args = [{"title": title, "body": "当前无该方向的可引用证据，不能构建实证论点。", "evidence_ids": [], "strength": 0.0}]
    return claims, args, "heuristic"


async def run_advocate(
    side: str, proposition: str, evidence: list[Evidence], *, mode: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """返回 (claims, arguments, agent_mode)。claims/arguments 为 dict 列表。"""
    required_stance = "pro" if side == "prosecution" else "con"
    if not any(e.stance == required_stance and e.strength > 0 for e in evidence):
        return heuristic_arguments(side, evidence)
    if mode == "llm":
        role = "Prosecutor"
        role_desc = T.PROSECUTOR_DESC if side == "prosecution" else T.DEFENSE_DESC
        stance_filter = {"pro", "neutral"} if side == "prosecution" else {"con", "neutral"}
        data = await llm_json(
            T.fill(T.ARGUMENT_SYSTEM, role=role, role_desc=role_desc),
            T.fill(T.ARGUMENT_USER, proposition=proposition, evidence=_fmt_evidence(evidence, stance_filter)),
            max_tokens=2500,
        )
        if isinstance(data, dict) and isinstance(data.get("arguments"), list):
            valid_ids = {e.id for e in evidence}
            allowed = {"pro", "neutral"} if side == "prosecution" else {"con", "neutral"}
            valid_ids = {e.id for e in evidence if e.stance in allowed}
            raw_claims = data.get("claims") if isinstance(data.get("claims"), list) else []
            claims = [
                {"text": clean_text(c.get("text"), 300), "evidence_ids": clean_ids(c.get("evidence_ids"), valid_ids, 6)}
                for c in raw_claims if isinstance(c, dict) and clean_text(c.get("text"), 300)
            ][:5]
            args = []
            for a in data["arguments"]:
                if not isinstance(a, dict):
                    continue
                title, body = clean_text(a.get("title"), 150), clean_text(a.get("body"), 1200)
                if not title or not body:
                    continue
                ev_ids = clean_ids(a.get("evidence_ids"), valid_ids, 6)
                strength = clean_score(a.get("strength")) if ev_ids else 0.0
                args.append(
                    {
                        "title": title,
                        "body": body,
                        "evidence_ids": ev_ids,
                        "strength": strength,
                    }
                )
            args = args[:4]
            if args:
                return claims, args, "llm"
    return heuristic_arguments(side, evidence)


def build_argument_rows(
    case_id: str,
    claims: list[dict] | None,
    args: list[dict] | None,
    side: str,
    *,
    claim_rows: list[Claim] | None = None,
) -> list[Argument]:
    """构建论证行，并以共享证据确定其规范 claim 引用。"""
    rows: list[Argument] = []
    available_claims = claim_rows or []
    for a in args or []:
        if not isinstance(a, dict) or not a.get("title") or not a.get("body"):
            continue
        strength = clean_score(a.get("strength"))
        evidence_ids = list(dict.fromkeys(
            e for e in (a.get("evidence_ids") if isinstance(a.get("evidence_ids"), list) else [])
            if isinstance(e, str)
        ))[:6]
        evidence_set = set(evidence_ids)
        claim_ids = [
            claim.id for claim in available_claims
            if claim.case_id == case_id and evidence_set.intersection(claim.evidence_ids or [])
        ]
        rows.append(
            Argument(
                id=gen_id("arg"),
                case_id=case_id,
                side=side,
                title=str(a.get("title", ""))[:200],
                body=str(a.get("body", ""))[:1200],
                evidence_ids=evidence_ids,
                claim_ids=claim_ids[:6],
                strength=strength,
            )
        )
    return rows


def build_claim_rows(case_id: str, claims: list[dict] | None, side: str) -> list[Claim]:
    """构建主张行；claims 缺失/非法时返回空列表而非抛 KeyError。"""
    rows: list[Claim] = []
    for c in claims or []:
        if not isinstance(c, dict) or not c.get("text"):
            continue
        rows.append(
            Claim(
                id=gen_id("clm"),
                case_id=case_id,
                side=side,
                text=str(c["text"])[:300],
                evidence_ids=list(dict.fromkeys(e for e in (c.get("evidence_ids") if isinstance(c.get("evidence_ids"), list) else []) if isinstance(e, str)))[:6],
            )
        )
    return rows
