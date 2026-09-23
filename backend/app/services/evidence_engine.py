"""Evidence Engine：可解释的证据可信度评分与来源排序。

原则：
- 不把"所有内容丢给 LLM 打分"；分数由确定性、可解释的加权公式得出。
- 权重集中在 WEIGHTS，可后续调整。
- UI 命名为「证据可信度 Evidence Confidence」，不宣称客观真理。
"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import urlparse

from app.models import Evidence, Source

# 权重集中配置（总和 1.0）
WEIGHTS: dict[str, float] = {
    "relevance": 0.28,
    "authority": 0.18,
    "community": 0.14,
    "recency": 0.12,
    "cross_validation": 0.13,
    "directness": 0.10,
    "source_independence": 0.05,
}

# 权威域名（外部来源）。知乎来源的权威度按内容类型与互动信号评估。
AUTHORITY_DOMAINS: dict[str, float] = {
    "gov.cn": 1.0, "who.int": 1.0, "nature.com": 1.0, "science.org": 1.0,
    "arxiv.org": 0.85, "ieee.org": 0.9, "acm.org": 0.9,
    "github.com": 0.6, "stackoverflow.com": 0.6,
    "medium.com": 0.35, "zhihu.com": 0.5, "weixin.qq.com": 0.4,
    "36kr.com": 0.45, "wikipedia.org": 0.7,
}


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _recency_score(published_at: str) -> float:
    """按发布时间衰减：2 年内基本满分，5 年以上趋近 0.3。"""
    if not published_at:
        return 0.5
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(published_at.strip()[: len(fmt)], fmt)
            years = max(0.0, (datetime.utcnow() - dt).days / 365.0)
            return clamp01(1.0 - max(0.0, years - 2.0) * 0.23)
        except ValueError:
            continue
    return 0.5


def _hostname(url: str) -> str:
    try:
        parsed = urlparse(url or "")
        return (parsed.hostname or "").lower().rstrip(".") if parsed.scheme in ("http", "https") else ""
    except ValueError:
        return ""


def _authority_score(source: Source) -> float:
    if getattr(source, "is_demo", False):
        return 0.0
    official_level = getattr(source, "official_authority_level", None)
    if isinstance(official_level, int) and not isinstance(official_level, bool) and 1 <= official_level <= 5:
        return round(official_level / 5, 2)
    if source.origin == "zhihu":
        # 知乎内容：回答 > 文章；互动信号参与评分，但不由点赞单独决定
        base = 0.45 if source.kind == "answer" else 0.4
        votes = source.vote_count
        if votes >= 2000:
            base += 0.25
        elif votes >= 500:
            base += 0.18
        elif votes >= 100:
            base += 0.1
        if source.summary and len(source.summary) > 400:
            base += 0.1  # 内容厚度信号
        return clamp01(base)
    # Web 来源：按域名
    host = _hostname(source.url)
    for domain, score in AUTHORITY_DOMAINS.items():
        if host == domain or host.endswith("." + domain):
            return score
    return 0.4


def _community_score(source: Source) -> float:
    """社区互动信号（对数尺度，避免唯点赞论）。"""
    import math

    votes = max(0, source.vote_count)
    comments = max(0, source.comment_count)
    score = math.log10(1 + votes) / 5.0 * 0.7 + math.log10(1 + comments) / 4.0 * 0.3
    return clamp01(score)


def score_source(source: Source, *, duplicate_count: int = 0, web_cross_confirmed: bool = False) -> float:
    """来源排序分（0~1）。duplicate_count：同题去重时被合并的次数（相关性佐证）。"""
    if getattr(source, "is_demo", False):
        return 0.0
    relevance = clamp01(source.relevance_score)
    authority = _authority_score(source)
    community = _community_score(source)
    recency = _recency_score(source.published_at)
    cross = 0.7 if web_cross_confirmed else 0.35
    directness = 0.7 if source.summary else 0.4
    independence = clamp01(source.independence_score) * (0.8 + 0.2 / (1 + duplicate_count))

    score = (
        WEIGHTS["relevance"] * relevance
        + WEIGHTS["authority"] * authority
        + WEIGHTS["community"] * community
        + WEIGHTS["recency"] * recency
        + WEIGHTS["cross_validation"] * cross
        + WEIGHTS["directness"] * directness
        + WEIGHTS["source_independence"] * independence
    )
    return round(clamp01(score), 4)


def score_evidence(
    evidence: Evidence,
    source: Source,
    *,
    corroborated_by: int = 0,
    contradicted_by: int = 0,
) -> float:
    """证据可信度（0~1）：来源质量 × 直接程度 × 独立佐证 − 被反例削弱。

    corroborated_by：独立来源中给出相同方向证据的数量
    contradicted_by：独立来源中给出相反方向证据的数量
    """
    if getattr(source, "is_demo", False):
        return 0.0
    source_quality = clamp01(source.rank_score or score_source(source))
    type_factor = {"fact": 1.0, "data": 0.9, "case": 0.7, "opinion": 0.55,
                   "prediction": 0.45, "assumption": 0.4, "limitation": 0.6}.get(
        evidence.evidence_type, 0.5
    )
    base = source_quality * (0.6 + 0.4 * type_factor)
    if corroborated_by > 0:
        base = clamp01(base + 0.08 * min(corroborated_by, 3))
    if contradicted_by > 0:
        base = clamp01(base - 0.1 * min(contradicted_by, 3))
    return round(clamp01(base), 4)


def _terms(text: str) -> set[str]:
    import re

    return set(re.findall(r"[\u4e00-\u9fff]{2,3}|[a-z0-9]{3,}", (text or "").lower()))


def evidence_relation(first: Evidence, second: Evidence) -> bool:
    """同语义、同方向、非演示且独立 hostname 的来源才互为佐证。"""
    left, right = getattr(first, "source", None), getattr(second, "source", None)
    if left is None or right is None or left.is_demo or right.is_demo:
        return False
    host_left, host_right = _hostname(left.url), _hostname(right.url)
    if not host_left or not host_right or host_left == host_right:
        return False
    if first.source_id == second.source_id or first.stance == "neutral" or first.stance != second.stance:
        return False
    a, b = _terms(first.claim), _terms(second.claim)
    return bool(a and b and len(a & b) / min(len(a), len(b)) >= 0.5)


def independence_via_domain(sources: list[Source]) -> None:
    """来源独立性：同域名多来源会降低彼此独立性得分（原地更新）。"""
    from collections import Counter

    domains = [_hostname(s.url) or f"unverified:{s.id}" for s in sources]
    counts = Counter(domains)
    for s, d in zip(sources, domains):
        n = counts[d]
        s.independence_score = 1.0 if n <= 1 else max(0.5, 1.0 - 0.15 * (n - 1))
