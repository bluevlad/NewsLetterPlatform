"""TechBriefing 시그널 기반 스코어링.

LLM 호출 없이 명시적 시그널(CVSS, project tier, days_old, source 가중치)만으로
0~10 스케일 importance score 산출. AllergyInsight 의 LLM importance_score 와
역할 동일이지만 도메인 시그널이 강하므로 충분히 일관성 있게 작동.

수식 (요약):
    base = 5.0
    + tier_weight    (S=+1.5, A=+1.0, B=+0.5)
    + source_weight  (cve=+2.5, github_release=+1.5, rss_blog=+0.8)
    + cvss_boost     (CVSS >= 9 → +2.0, >=7 → +1.0, >=4 → +0.5)
    + breaking_boost (release.is_breaking → +1.0)
    - age_penalty    (days_old / 7, max 1.5)
    clamp [0, 10]
"""

from datetime import datetime, timezone
from typing import Any, Dict, List

from .config import PROJECT_TIER_WEIGHT


_SOURCE_WEIGHT = {
    "nvd_cve":        2.5,
    "github_release": 1.5,
    "rss_blog":       0.8,
}


def _days_old(published: Any) -> float:
    if not published:
        return 0.0
    if isinstance(published, str):
        try:
            published = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except Exception:
            return 0.0
    if not isinstance(published, datetime):
        return 0.0
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - published
    return max(0.0, delta.total_seconds() / 86400.0)


def score_item(item: Dict[str, Any]) -> float:
    """단일 아이템 importance score 계산."""
    base = 5.0

    tier = item.get("tier") or "B"
    tier_weight_map = {"S": 1.5, "A": 1.0, "B": 0.5}
    base += tier_weight_map.get(tier, 0.5)

    source = item.get("source") or ""
    base += _SOURCE_WEIGHT.get(source, 0.0)

    if source == "nvd_cve":
        cvss = item.get("cvss")
        if isinstance(cvss, (int, float)):
            if cvss >= 9.0:
                base += 2.0
            elif cvss >= 7.0:
                base += 1.0
            elif cvss >= 4.0:
                base += 0.5

    if source == "github_release" and item.get("is_breaking"):
        base += 1.0

    days = _days_old(item.get("published_at"))
    age_penalty = min(1.5, days / 7.0)
    base -= age_penalty

    return max(0.0, min(10.0, round(base, 2)))


def annotate_scores(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """리스트 in-place: importance_score 키 추가."""
    for it in items:
        it["importance_score"] = score_item(it)
    return items
