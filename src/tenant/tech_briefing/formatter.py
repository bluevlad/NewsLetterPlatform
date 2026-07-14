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

from .analyzer import analyze_headlines
from .config import ECOSYSTEM_META
from .scorer import annotate_scores
from .translator import translate_headlines

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
    # URL/마크다운 잔여 토큰 — AI repo 릴리즈 노트에 링크가 많아 빈출
    "https", "http", "www", "com", "org", "github", "html", "href",
    # 추가 일반 불용어 (릴리즈 노트 산문에서 빈출)
    "that", "this", "when", "not", "now", "can", "will", "has", "have",
    "full", "changelog", "compare", "pull", "merge", "branch",
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
        # 번역기(translator)가 채운 한글 필드 — 없으면 빈 문자열(템플릿이 영문 fallback).
        "title_ko":        (item.get("title_ko") or "").strip(),
        "summary_ko":      (item.get("summary_ko") or "").strip(),
        "service_tags":    _service_tags(item),
    }


def _extract_keywords(text: str) -> List[str]:
    if not text:
        return []
    # URL 은 통째로 제거 — 경로 조각(github/blob/releases 등)이 키워드로 오르지 않게
    text = re.sub(r"https?://\S+", " ", text)
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

    # Today's deep-dive cards 한도 (사용자 결정: 5건)
    HEADLINE_LIMIT = 5
    # 카테고리별 digest 한도 — 너무 길어지지 않게 항목당 cap.
    DIGEST_PER_GROUP_LIMIT = 12
    # 보안 "기타"(운영 서비스 미적용) CVE 한도 — 적용 프로젝트보다 낮게.
    DIGEST_CVE_OTHER_LIMIT = 8
    # 푸터 미니 리스트
    FOOTER_DEPRECATION_LIMIT = 5
    FOOTER_KEYWORD_LIMIT = 8

    def format(self, collected_data: Dict[str, Any]) -> Dict[str, Any]:
        payload = (collected_data or {}).get("tech_daily") or {}
        if not payload:
            return self._empty_context()

        github_releases: List[Dict[str, Any]] = payload.get("github_releases") or []
        cves:            List[Dict[str, Any]] = payload.get("cves") or []
        rss_articles:    List[Dict[str, Any]] = payload.get("rss_articles") or []

        # 시그널 스코어 부여 (service_relevance 포함).
        annotate_scores(github_releases)
        annotate_scores(cves)
        annotate_scores(rss_articles)

        all_items = [*github_releases, *cves, *rss_articles]

        # Today's 5 — 1프로젝트 1, importance + relevance 기준.
        headlines = self._select_headlines(all_items, self.HEADLINE_LIMIT)

        # 헤드라인 제목/요약 한글 번역 (LLM, Ollama 로컬) — 배치 1콜로 in-place enrich.
        # analyzer 와 독립 — Service Profile 매칭 여부 무관하게 전 항목 커버.
        # 비활성/실패 시 item['title_ko'] 미설정 → 템플릿이 영문 fallback.
        try:
            translate_headlines(headlines)
        except Exception as e:
            logger.exception("translate_headlines 예외 — 영문 fallback: %s", e)

        # Deep analysis (LLM, Ollama 로컬) — top N 헤드라인만 in-place enrich.
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

        # 카테고리별 digest — 헤드라인에 안 들어간 항목들만.
        def _not_in_headlines(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            return [x for x in items if x.get("dedup_key") not in headline_keys]

        digest_groups: List[Dict[str, Any]] = []

        # 릴리즈 — 프로젝트 단위로 버전 통합 (#1).
        rel_consolidated = self._consolidate_releases(
            _not_in_headlines(github_releases)
        )[: self.DIGEST_PER_GROUP_LIMIT]
        if rel_consolidated:
            digest_groups.append({
                "label": "릴리즈", "color": "#15803d", "bg": "#dcfce7",
                "entries": rel_consolidated,
            })

        # 보안 CVE — 운영 서비스 적용 / 기타 2분할 (#2).
        cve_sorted = self._sort_by_score(_not_in_headlines(cves))
        cve_applied, cve_other = self._split_cves_by_relevance(cve_sorted)
        if cve_applied:
            digest_groups.append({
                "label": "🎯 보안 · 적용 프로젝트", "color": "#b91c1c", "bg": "#fee2e2",
                "entries": [_enrich(x) for x in cve_applied[: self.DIGEST_PER_GROUP_LIMIT]],
            })
        if cve_other:
            digest_groups.append({
                "label": "보안 · 기타", "color": "#9f1239", "bg": "#ffe4e6",
                "entries": [_enrich(x) for x in cve_other[: self.DIGEST_CVE_OTHER_LIMIT]],
            })

        # 공식 블로그.
        rss_remainder = self._sort_by_score(
            _not_in_headlines(rss_articles)
        )[: self.DIGEST_PER_GROUP_LIMIT]
        if rss_remainder:
            digest_groups.append({
                "label": "공식 블로그", "color": "#0e7490", "bg": "#cffafe",
                "entries": [_enrich(x) for x in rss_remainder],
            })

        digest_total = sum(len(g["entries"]) for g in digest_groups)

        # 푸터 미니 리스트 — deprecations + 키워드 트렌드 (집중도에서 분리).
        deprecation_items = self._sort_by_score(
            [r for r in github_releases if r.get("has_deprecation")]
        )[: self.FOOTER_DEPRECATION_LIMIT]
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
            "digest_groups":     digest_groups,           # [{label, items[], color, bg}]
            "digest_total":      digest_total,
            "footer_extras": {
                "deprecations":  [_enrich(r) for r in deprecation_items],
                "keywords":      keywords_rising,
            },
            "service_summary":   service_summary,
            "stats": {
                "release_count": stats.get("release_count", len(github_releases)),
                "cve_count":     stats.get("cve_count",     len(cves)),
                "rss_count":     stats.get("rss_count",     len(rss_articles)),
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
            "footer_extras": {"deprecations": [], "keywords": []},
            "service_summary": {},
            "stats": {
                "release_count": 0, "cve_count": 0, "rss_count": 0,
                "headline_count": 0, "digest_count": 0, "total_items": 0,
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

    def _consolidate_releases(
        self, items: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """릴리즈 digest 항목을 project 단위로 통합 (#1).

        같은 프로젝트(예: hibernate-orm)의 여러 버전을 한 줄로 합친다.
        - 대표 = 프로젝트 내 최신 발행(published_at desc) 1건 → _enrich
        - 나머지 버전 수 = extra_count 로 축약 ("외 N개 버전")
        - breaking/deprecation 은 그룹 OR — 한 버전이라도 있으면 배지 유지
        - 그룹 간 정렬은 멤버 최고 점수(importance + relevance) 기준
        """
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for it in items:
            key = it.get("project") or it.get("project_short") or it.get("title", "")
            groups.setdefault(key, []).append(it)

        consolidated: List[Dict[str, Any]] = []
        for members in groups.values():
            # 최신 발행 우선 → 첫 항목이 대표(최신 버전).
            members.sort(
                key=lambda x: _parse_dt(x.get("published_at")).timestamp()
                if x.get("published_at") else 0.0,
                reverse=True,
            )
            entry = _enrich(members[0])
            entry["consolidated"] = True
            entry["version_count"] = len(members)
            entry["extra_count"] = len(members) - 1
            entry["group_breaking"] = any(m.get("is_breaking") for m in members)
            entry["group_deprecation"] = any(
                m.get("has_deprecation") for m in members
            )
            entry["group_score"] = max(
                m.get("importance_score", 0.0) + m.get("relevance_max", 0.0)
                for m in members
            )
            consolidated.append(entry)

        consolidated.sort(key=lambda e: e["group_score"], reverse=True)
        return consolidated

    @staticmethod
    def _split_cves_by_relevance(
        cves: List[Dict[str, Any]],
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """CVE 리스트를 (운영 서비스 적용, 기타)로 분리 (#2).

        service_relevance 매칭(=service_tags 존재) 여부로 가른다.
        입력 순서를 보존하므로 호출 전 정렬된 순서가 유지된다.
        """
        applied = [c for c in cves if _service_tags(c)]
        other = [c for c in cves if not _service_tags(c)]
        return applied, other

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

        # 푸터 미니 리스트용 — caller 에서 추가로 cap 가능.
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
