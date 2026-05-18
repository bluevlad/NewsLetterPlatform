"""TechBriefing 포매터 — 3 sources 통합 → 3 섹션 컨텍스트.

섹션:
  1. 헤드라인 (top 5, 1프로젝트 1)
  2. 릴리즈 & 보안 (4분류: new_releases / breaking_changes / cves / deprecations)
  3. 키워드 트렌드 (rising / declining)
"""

import logging
import re
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List

from .config import ECOSYSTEM_META
from .scorer import annotate_scores

logger = logging.getLogger(__name__)


# 키워드 추출용 — 제목에서 의미 있는 토큰만 살림.
# 너무 일반적인 단어 / 버전 숫자 / 한 글자 단어 제외.
_STOPWORDS = {
    "the", "a", "an", "of", "in", "for", "to", "and", "or", "with",
    "is", "are", "was", "were", "be", "by", "on", "at", "from",
    "release", "released", "version", "update", "fix", "fixes", "fixed",
    "new", "support", "supports", "added", "add", "use", "using", "used",
    "introducing", "announcing", "what", "we", "you",
    "ga", "rc", "beta", "alpha", "milestone",
}
_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            pass
    return datetime.now()


def _ecosystem_meta(eco: str) -> Dict[str, str]:
    return ECOSYSTEM_META.get(
        eco or "tooling", {"label": eco or "기타", "color": "#475569", "bg": "#f1f5f9"}
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
    eco_meta = _ecosystem_meta(item.get("ecosystem", ""))
    published = _parse_dt(item.get("published_at")) if item.get("published_at") else None
    return {
        **item,
        "ecosystem_label": eco_meta["label"],
        "ecosystem_color": eco_meta["color"],
        "ecosystem_bg":    eco_meta["bg"],
        "published_dt":    published,
        "published_display": published.strftime("%m-%d") if published else "—",
        "title_safe":      item.get("title") or "(제목 없음)",
        "summary_safe":    (item.get("summary") or "").strip(),
        "service_tags":    _service_tags(item),
    }


def _extract_keywords(text: str) -> List[str]:
    if not text:
        return []
    tokens = _TOKEN_PATTERN.findall(text.lower())
    out = []
    for t in tokens:
        if t in _STOPWORDS:
            continue
        if t.isdigit():
            continue
        # version-like (1.0.0, v3, 3.4) 제외
        if re.fullmatch(r"v?\d+(\.\d+)*", t):
            continue
        out.append(t)
    return out


class TechBriefingFormatter:
    """3 sources → daily 컨텍스트."""

    HEADLINE_LIMIT = 5
    RELEASES_LIMIT = 8
    BREAKING_LIMIT = 5
    CVE_LIMIT = 6
    DEPRECATION_LIMIT = 5
    KEYWORD_TREND_LIMIT = 6

    def format(self, collected_data: Dict[str, Any]) -> Dict[str, Any]:
        payload = (collected_data or {}).get("tech_daily") or {}
        if not payload:
            return self._empty_context()

        github_releases: List[Dict[str, Any]] = payload.get("github_releases") or []
        cves:            List[Dict[str, Any]] = payload.get("cves") or []
        rss_articles:    List[Dict[str, Any]] = payload.get("rss_articles") or []

        # 시그널 스코어 부여.
        annotate_scores(github_releases)
        annotate_scores(cves)
        annotate_scores(rss_articles)

        all_items = [
            *github_releases,
            *cves,
            *rss_articles,
        ]
        # 헤드라인: 1프로젝트 1, importance 기준 top 5.
        headlines = self._select_headlines(all_items, self.HEADLINE_LIMIT)

        # 릴리즈 & 보안 4분류.
        new_releases     = self._sort_by_score([r for r in github_releases
                                                if not r.get("is_breaking")])
        breaking_changes = self._sort_by_score([r for r in github_releases
                                                if r.get("is_breaking")])
        cve_sorted       = self._sort_by_score(cves)
        deprecations     = self._sort_by_score([
            r for r in github_releases if r.get("has_deprecation")
        ])

        # 키워드 트렌드 (rising) — 모든 아이템 제목·요약에서 추출 → 빈도 top.
        rising_keywords = self._compute_rising_keywords(all_items)

        report_date = _parse_dt(payload.get("report_date"))
        stats = payload.get("stats") or {}

        # 서비스별 관련 헤드라인 카운트 — 메일 헤더에 한 줄 노출용.
        service_summary: Dict[str, int] = {}
        for h in headlines:
            for tag in _service_tags(h):
                service_summary[tag["service"]] = service_summary.get(tag["service"], 0) + 1

        # 비어 있어도 섹션이 자동 숨김되도록 falsy 빈 dict/list.
        return {
            "report_date": report_date,
            "headlines": [_enrich(h) for h in headlines],
            "releases_security": {
                "new_releases":     [_enrich(r) for r in new_releases[: self.RELEASES_LIMIT]],
                "breaking_changes": [_enrich(r) for r in breaking_changes[: self.BREAKING_LIMIT]],
                "cves":             [_enrich(r) for r in cve_sorted[: self.CVE_LIMIT]],
                "deprecations":     [_enrich(r) for r in deprecations[: self.DEPRECATION_LIMIT]],
                "total":            (
                    min(len(new_releases), self.RELEASES_LIMIT)
                    + min(len(breaking_changes), self.BREAKING_LIMIT)
                    + min(len(cve_sorted), self.CVE_LIMIT)
                    + min(len(deprecations), self.DEPRECATION_LIMIT)
                ),
            },
            "keywords_rising":   rising_keywords,
            "service_summary":   service_summary,    # {"hopenvision": 3}
            "stats": {
                "release_count": stats.get("release_count", len(github_releases)),
                "cve_count":     stats.get("cve_count",     len(cves)),
                "rss_count":     stats.get("rss_count",     len(rss_articles)),
                "headline_count": len(headlines),
                "total_items":   len(all_items),
            },
            "generated_at": report_date,
        }

    # ─── helpers ───

    def _empty_context(self) -> Dict[str, Any]:
        return {
            "report_date": datetime.now(),
            "headlines": [],
            "releases_security": {
                "new_releases": [], "breaking_changes": [],
                "cves": [], "deprecations": [], "total": 0,
            },
            "keywords_rising": [],
            "service_summary": {},
            "stats": {
                "release_count": 0, "cve_count": 0, "rss_count": 0,
                "headline_count": 0, "total_items": 0,
            },
            "generated_at": datetime.now(),
        }

    def _sort_by_score(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """importance + relevance 합산 기준 정렬.

        relevance_max 가 0(매칭 없음)이면 importance 만으로 정렬 — 기존 동작과 동일.
        Service Profile 이 있으면 운영 서비스 관련 항목이 위로 올라옴.
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
        """1 프로젝트 1 헤드라인 + 카테고리 다양성 (max_per_eco=2)."""
        ranked = self._sort_by_score(items)
        seen_projects: set[str] = set()
        eco_count: Counter = Counter()
        kept: List[Dict[str, Any]] = []
        for it in ranked:
            project = (it.get("project") or "").lower()
            eco = it.get("ecosystem") or ""
            if project and project in seen_projects:
                continue
            if eco and eco_count[eco] >= 2:
                continue
            kept.append(it)
            if project:
                seen_projects.add(project)
            if eco:
                eco_count[eco] += 1
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

        top = weighted.most_common(self.KEYWORD_TREND_LIMIT * 2)
        # tier S 가중치를 받은 키워드가 상위로 자연스럽게 올라옴.
        rising = []
        for kw, weight in top[: self.KEYWORD_TREND_LIMIT]:
            co = [k for k, _ in co_occurrence.get(kw, Counter()).most_common(3)]
            rising.append({
                "keyword": kw,
                "weight": round(weight, 1),
                "co_keywords": co,
            })
        return rising
