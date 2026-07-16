"""TechBriefing 시그널 기반 스코어링 + Service Relevance 평가.

LLM 호출 없이 명시적 시그널(카테고리 가중, 모집·마감 신호, 공식 출처,
days_old)만으로 0~10 스케일 importance score 산출.

추가로 Service Profile 시그널과 매칭하여 서비스별 relevance 점수를 부여 —
헤드라인 선별과 메일 카드 태깅에 사용. (교육·커리어 도메인에서는 매칭이
드물지만 메커니즘은 범용이라 유지.)

importance_score 수식 (요약):
    base = 5.0
    + category_weight  (policy=+1.5, course=+1.2, seminar=+1.0, news=+0.8)
    + recruiting_boost (모집/신청/접수/마감 힌트 → +1.0)
    + official_boost   (정부 출처 korea.kr → +0.7)
    - age_penalty      (days_old / 2, max 2.0 — 뉴스 사이클 기준)
    clamp [0, 10]

service_relevance 수식 (per 서비스):
    + per_high_interest × matched_high (cap high_cap)
    + per_known_debt    × matched_debt (cap debt_cap)
    - per_low_interest  × matched_low
"""

from datetime import datetime, timezone
from typing import Any, Dict, List

from .config import CATEGORY_WEIGHT
from .service_profiles import ServiceProfile, load_profiles


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

    category = item.get("category") or "news"
    base += CATEGORY_WEIGHT.get(category, 0.8)

    if item.get("is_recruiting"):
        base += 1.0

    origin = (item.get("origin") or "") + " " + (item.get("url") or "")
    if "korea.kr" in origin or "정책브리핑" in origin:
        base += 0.7

    days = _days_old(item.get("published_at"))
    age_penalty = min(2.0, days / 2.0)
    base -= age_penalty

    return max(0.0, min(10.0, round(base, 2)))


def _haystack(item: Dict[str, Any]) -> str:
    """매칭 대상 텍스트 — title + summary (소문자)."""
    return (
        (item.get("title") or "") + " " +
        (item.get("summary") or "")
    ).lower()


def evaluate_relevance(
    item: Dict[str, Any], profile: ServiceProfile
) -> Dict[str, Any]:
    """단일 아이템 × 단일 서비스 profile → relevance 평가.

    Returns:
        {
            "score": float,                    # 음수 가능 (low_interest 매칭 시)
            "matched_high":  list[str],
            "matched_low":   list[str],
            "matched_debt":  list[str],        # area 명
            "reason": str,                     # 메일 카드에 한 줄 노출용
        }
    """
    if not profile.has_signals:
        return {"score": 0.0, "matched_high": [], "matched_low": [],
                "matched_debt": [], "reason": ""}

    hay = _haystack(item)
    w = profile.weights

    matched_high = [s for s in profile.high_interest if s.lower() in hay]
    matched_low  = [s for s in profile.low_interest  if s.lower() in hay]
    matched_debt = [d.area for d in profile.known_debt if d.area.lower() in hay]

    high_score = min(len(matched_high) * w.per_high_interest, w.high_cap)
    debt_score = min(len(matched_debt) * w.per_known_debt,    w.debt_cap)
    low_penalty = len(matched_low) * w.per_low_interest

    score = round(high_score + debt_score - low_penalty, 2)

    # reason 한 줄 (상위 시그널 우선)
    bits: list[str] = []
    if matched_debt:
        bits.append(f"부채매칭: {matched_debt[0]}")
    if matched_high:
        # 가장 긴 매칭 시그널이 보통 가장 구체적 (e.g., "Spring Boot 3" > "Spring")
        bits.append(f"관심: {sorted(matched_high, key=len, reverse=True)[0]}")
    if matched_low:
        bits.append(f"비관심: {matched_low[0]}")

    return {
        "score": score,
        "matched_high": matched_high,
        "matched_low":  matched_low,
        "matched_debt": matched_debt,
        "reason": " · ".join(bits),
    }


def annotate_scores(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """리스트 in-place: importance_score + service_relevance 키 추가.

    service_relevance = { "<service>": {score, matched_high, ...}, ... }
    프로파일이 없으면 service_relevance = {} (기존 동작과 동일).
    """
    profiles = load_profiles()
    for it in items:
        it["importance_score"] = score_item(it)
        if profiles:
            rel_map: Dict[str, Dict[str, Any]] = {}
            for p in profiles:
                rel_map[p.service] = evaluate_relevance(it, p)
            it["service_relevance"] = rel_map
            # 헤드라인 정렬용 — 서비스 중 최대 relevance (음수 허용 → 비관심 매칭은
            # 자연스럽게 후순위로 밀림).
            it["relevance_max"] = max(
                (r["score"] for r in rel_map.values()),
                default=0.0,
            )
        else:
            it["service_relevance"] = {}
            it["relevance_max"] = 0.0
    return items
