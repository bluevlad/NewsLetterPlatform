"""TechBriefing 포매터 — 3 sources 통합 → 3 섹션 컨텍스트.

섹션:
  1. 헤드라인 (top 5, 카테고리 다양성)
  2. 카테고리별 다이제스트 (교육과정 / 세미나·행사 / 정책·지원 / 뉴스)
  3. 푸터 미니 리스트 (모집·마감 임박 / 키워드 트렌드)

콘텐츠가 한국어(뉴스/정책/교육)라 번역 단계는 없다 — deep analysis(LLM)만 수행.
"""

import logging
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List

from .analyzer import analyze_headlines
from .config import CATEGORY_META
from .scorer import annotate_scores

logger = logging.getLogger(__name__)


# 키워드 추출용 — 제목에서 의미 있는 토큰만 살림 (한글 + 영문).
# 검색 키워드 자체와 뉴스 상투어는 매일 빈출이라 제외.
_STOPWORDS = {
    # 영문 일반
    "the", "a", "an", "of", "in", "for", "to", "and", "or", "with",
    "is", "are", "was", "were", "be", "by", "on", "at", "from",
    "new", "news", "https", "http", "www", "com", "org",
    "nbsp", "amp", "quot", "apos",
    # 검색 키워드 구성어 — 전 항목 공통이라 트렌드 신호가 아님
    "인공지능", "교육", "강의", "부트캠프", "세미나", "컨퍼런스",
    "생성형", "디지털", "정책",
    # 뉴스 상투어
    "기자", "뉴스", "오늘", "지난", "이번", "관련", "위한", "통해",
    "대한", "대해", "위해", "함께", "국내", "최초", "개최", "진행",
    "지원", "사업", "발표", "공개", "출시", "모집", "신청", "마감",
}
_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}|[가-힣]{2,}")


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            pass
    return datetime.now()


def _category_meta(category: str) -> Dict[str, str]:
    return CATEGORY_META.get(
        category or "news",
        {"label": category or "기타", "color": "#475569", "bg": "#f1f5f9"},
    )


def _service_tags(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """service_relevance dict → 메일 카드용 태그 리스트.

    음수 relevance(=low_interest 매칭만 된 경우)는 표시 안 함.
    score 가 0 이면 매칭이 전혀 없는 것 — 태그 생성 안 함.
    """
    rel = item.get("service_relevance") or {}
    tags: List[Dict[str, Any]] = []
    for service, info in rel.items():
        score = float(info.get("score", 0.0))
        if score <= 0:
            continue
        tags.append({
            "service": service,
            "score":   round(score, 1),
            "reason":  info.get("reason") or "",
        })
    # 점수 높은 순
    tags.sort(key=lambda t: t["score"], reverse=True)
    return tags


def _enrich(item: Dict[str, Any]) -> Dict[str, Any]:
    meta = _category_meta(item.get("category", ""))
    published = _parse_dt(item.get("published_at")) if item.get("published_at") else None
    return {
        **item,
        "category_label": meta["label"],
        "category_color": meta["color"],
        "category_bg":    meta["bg"],
        "published_dt":    published,
        "published_display": published.strftime("%m-%d") if published else "—",
        "title_safe":      item.get("title") or "(제목 없음)",
        "summary_safe":    (item.get("summary") or "").strip(),
        "service_tags":    _service_tags(item),
    }


# 조사로 끝나는 토큰 필터 — SkillRadar LLM 요약(장문 한국어 산문)에서
# '기술을', '있는' 같은 곡용형이 키워드로 오르는 것 방지.
_PARTICLE_ENDINGS = ("을", "를", "은", "는", "이", "가", "의", "에", "로", "와", "과", "도")


def _extract_keywords(text: str) -> List[str]:
    if not text:
        return []
    # URL 은 통째로 제거 — 경로 조각이 키워드로 오르지 않게
    text = re.sub(r"https?://\S+", " ", text)
    tokens = _TOKEN_PATTERN.findall(text)
    out = []
    for t in tokens:
        lowered = t.lower()
        if lowered in _STOPWORDS:
            continue
        if lowered.isdigit():
            continue
        # 한글 곡용형 제외 — 용언 활용('~다' 종결)과 조사 결합형은
        # 트렌드 신호가 아니라 문장 구성 요소.
        if re.fullmatch(r"[가-힣]+", t) and (
            t.endswith("다") or t.endswith(_PARTICLE_ENDINGS)
        ):
            continue
        out.append(lowered)
    return out


class TechBriefingFormatter:
    """3 sources → daily 컨텍스트."""

    # Today's deep-dive cards 한도 (사용자 결정: 5건)
    HEADLINE_LIMIT = 5
    # 헤드라인 카테고리 다양성 — 같은 카테고리 최대 2건.
    HEADLINE_MAX_PER_CATEGORY = 2
    # 카테고리별 digest 한도 — 너무 길어지지 않게 항목당 cap.
    DIGEST_PER_GROUP_LIMIT = 10
    # 푸터 미니 리스트
    FOOTER_RECRUITING_LIMIT = 5
    FOOTER_KEYWORD_LIMIT = 8

    def format(self, collected_data: Dict[str, Any]) -> Dict[str, Any]:
        payload = (collected_data or {}).get("tech_daily") or {}
        if not payload:
            return self._empty_context()

        news_items:   List[Dict[str, Any]] = payload.get("news_items") or []
        policy_items: List[Dict[str, Any]] = payload.get("policy_items") or []
        course_items: List[Dict[str, Any]] = payload.get("course_items") or []

        # 시그널 스코어 부여 (service_relevance 포함).
        annotate_scores(news_items)
        annotate_scores(policy_items)
        annotate_scores(course_items)

        all_items = [*policy_items, *course_items, *news_items]

        # Today's 5 — 카테고리 다양성, importance + relevance 기준.
        headlines = self._select_headlines(all_items, self.HEADLINE_LIMIT)

        # Deep analysis (LLM, Ollama 로컬) — 헤드라인 in-place enrich.
        # 비활성/실패 시 item['analysis'] = None → 템플릿이 summary fallback.
        try:
            analyzed = analyze_headlines(headlines)
            if analyzed:
                logger.info("TechBriefing deep analysis: %d/%d 카드 분석 성공",
                            analyzed, len(headlines))
        except Exception as e:
            logger.exception("analyze_headlines 예외 — 모든 카드 fallback: %s", e)
            for h in headlines:
                h.setdefault("analysis", None)

        # 헤드라인 dedup_key 셋 — digest 에서 동일 항목 제외용.
        headline_keys = {h.get("dedup_key") for h in headlines if h.get("dedup_key")}

        def _not_in_headlines(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            return [x for x in items if x.get("dedup_key") not in headline_keys]

        # 카테고리별 digest — 헤드라인에 안 들어간 항목들만.
        digest_groups: List[Dict[str, Any]] = []
        by_category: Dict[str, List[Dict[str, Any]]] = {}
        for it in _not_in_headlines(all_items):
            by_category.setdefault(it.get("category") or "news", []).append(it)

        for category in ("course", "seminar", "policy", "news"):
            members = self._sort_by_score(by_category.get(category) or [])
            if not members:
                continue
            meta = _category_meta(category)
            digest_groups.append({
                "label": meta["label"], "color": meta["color"], "bg": meta["bg"],
                "entries": [_enrich(x) for x in members[: self.DIGEST_PER_GROUP_LIMIT]],
            })

        digest_total = sum(len(g["entries"]) for g in digest_groups)

        # 푸터 미니 리스트 — 모집·마감 임박 + 키워드 트렌드.
        recruiting_items = self._sort_by_score(
            [x for x in all_items if x.get("is_recruiting")]
        )[: self.FOOTER_RECRUITING_LIMIT]
        keywords_rising = self._compute_rising_keywords(all_items)[: self.FOOTER_KEYWORD_LIMIT]

        report_date = _parse_dt(payload.get("report_date"))
        stats = payload.get("stats") or {}

        # 서비스별 관련 헤드라인 카운트.
        service_summary: Dict[str, int] = {}
        for h in headlines:
            for tag in _service_tags(h):
                service_summary[tag["service"]] = service_summary.get(tag["service"], 0) + 1

        return {
            "report_date": report_date,
            "headlines": [_enrich(h) for h in headlines],
            "digest_groups":     digest_groups,           # [{label, entries[], color, bg}]
            "digest_total":      digest_total,
            "footer_extras": {
                "recruiting":    [_enrich(x) for x in recruiting_items],
                "keywords":      keywords_rising,
            },
            "service_summary":   service_summary,
            "stats": {
                "news_count":   stats.get("news_count",   len(news_items)),
                "policy_count": stats.get("policy_count", len(policy_items)),
                "course_count": stats.get("course_count", len(course_items)),
                "headline_count": len(headlines),
                "digest_count":  digest_total,
                "total_items":   len(all_items),
            },
            "generated_at": report_date,
        }

    # ─── helpers ───

    def _empty_context(self) -> Dict[str, Any]:
        return {
            "report_date": datetime.now(),
            "headlines": [],
            "digest_groups": [],
            "digest_total": 0,
            "footer_extras": {"recruiting": [], "keywords": []},
            "service_summary": {},
            "stats": {
                "news_count": 0, "policy_count": 0, "course_count": 0,
                "headline_count": 0, "digest_count": 0, "total_items": 0,
            },
            "generated_at": datetime.now(),
        }

    def _sort_by_score(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """importance + relevance 합산 기준 정렬.

        relevance_max 가 0(매칭 없음)이면 importance 만으로 정렬.
        """
        return sorted(
            items,
            key=lambda x: (
                x.get("importance_score", 0.0) + x.get("relevance_max", 0.0),
                # tie-breaker: 더 최신 먼저
                _parse_dt(x.get("published_at")).timestamp() if x.get("published_at") else 0,
            ),
            reverse=True,
        )

    def _select_headlines(
        self, items: List[Dict[str, Any]], limit: int
    ) -> List[Dict[str, Any]]:
        """카테고리 다양성 (max_per_category=2) + 제목 중복 제거."""
        ranked = self._sort_by_score(items)
        seen_titles: set[str] = set()
        category_count: Counter = Counter()
        kept: List[Dict[str, Any]] = []
        for it in ranked:
            title = (it.get("title") or "").strip().lower()
            category = it.get("category") or "news"
            if title and title in seen_titles:
                continue
            if category_count[category] >= self.HEADLINE_MAX_PER_CATEGORY:
                continue
            kept.append(it)
            if title:
                seen_titles.add(title)
            category_count[category] += 1
            if len(kept) >= limit:
                break
        return kept

    def _compute_rising_keywords(
        self, items: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """제목 + 요약에서 토큰 빈도. importance_score 가중치 부여."""
        weighted: Counter = Counter()
        co_occurrence: Dict[str, Counter] = {}
        for it in items:
            tokens = set(
                _extract_keywords(it.get("title", ""))
                + _extract_keywords(it.get("summary", ""))[:25]
            )
            score = float(it.get("importance_score", 5.0))
            for t in tokens:
                weighted[t] += score
            # co-occurrence: 같은 아이템의 다른 토큰들
            for t in tokens:
                co_occurrence.setdefault(t, Counter())
                for other in tokens:
                    if other != t:
                        co_occurrence[t][other] += 1

        max_n = max(self.FOOTER_KEYWORD_LIMIT * 2, 16)
        top = weighted.most_common(max_n)
        rising = []
        for kw, weight in top[: self.FOOTER_KEYWORD_LIMIT]:
            co = [k for k, _ in co_occurrence.get(kw, Counter()).most_common(3)]
            rising.append({
                "keyword": kw,
                "weight": round(weight, 1),
                "co_keywords": co,
            })
        return rising
