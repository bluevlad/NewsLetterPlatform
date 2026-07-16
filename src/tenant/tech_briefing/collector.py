"""TechBriefing 데이터 수집기 — 3 sources (뉴스 검색 / 정책 / 교육·세미나).

수집 도메인: AI 학습·커리어 (SkillRadar 수집 대상과 동일 기반 — config 참조).
각 소스는 독립적으로 fail-safe — 한쪽 실패해도 다른 섹션은 살아남는다.
LLM 호출은 수집 범위 외 (RSS summary 그대로 사용, truncate).

수집 경로:
  - 뉴스/정책/교육 키워드 → Google News RSS 검색 (키 불필요, ko/KR)
  - 정책 RSS(korea.kr) → 정책 키워드 1차 필터
"""

import asyncio
import html as html_mod
import logging
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx

from .config import (
    COURSE_KEYWORDS,
    COURSE_RSS_FEEDS,
    MAX_PER_KEYWORD,
    NEWS_KEYWORDS,
    POLICY_KEYWORDS,
    POLICY_RSS_FEEDS,
    RSS_MAX_ITEMS,
    RECRUITING_HINTS,
    SEMINAR_HINTS,
)
from ...common.utils import retry_async

logger = logging.getLogger(__name__)

API_TIMEOUT = 30.0
_USER_AGENT = "NewsLetterPlatform/1.0 (TechBriefing)"


def _strip_html(text: str, max_len: int = 320) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = html_mod.unescape(cleaned)  # &nbsp; 등 엔티티 잔여물 제거
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1].rstrip() + "…"
    return cleaned


def _parse_rss_date(entry: Dict[str, Any]) -> Optional[datetime]:
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if struct:
        return datetime(*struct[:6], tzinfo=timezone.utc)
    return None


def _classify_course(title: str) -> str:
    """교육 키워드 결과를 course/seminar 로 분류 (SkillRadar course.py 와 동일 힌트)."""
    lowered = (title or "").lower()
    if any(h.lower() in lowered for h in SEMINAR_HINTS):
        return "seminar"
    return "course"


def _is_recruiting(title: str) -> bool:
    return any(h in (title or "") for h in RECRUITING_HINTS)


def _make_item(
    *,
    source: str,
    category: str,
    keyword: str,
    origin: str,
    title: str,
    url: str,
    published_at: Optional[datetime],
    summary: str,
) -> Dict[str, Any]:
    return {
        "source": source,            # news | policy | course
        "category": category,        # news | policy | course | seminar
        "keyword": keyword,
        "origin": origin,            # 출처 라벨 (구글뉴스 / 정책브리핑 / feed host)
        "title": title or "(제목 없음)",
        "url": url or "",
        "published_at": published_at,
        "summary": summary,
        "is_recruiting": _is_recruiting(title),
        "dedup_key": f"{category}:{url or title}",
    }


class TechBriefingCollector:
    """3 sources 비동기 수집 → 정규화 → dict 반환."""

    def __init__(self):
        self._metrics: list[dict] = []

    def drain_metrics(self) -> list[dict]:
        m, self._metrics = self._metrics, []
        return m

    @contextmanager
    def _track(self, *, data_type: str, api_path: str):
        started = time.monotonic()
        metric: dict = {
            "data_type": data_type,
            "api_path": api_path,
            "raw_count": 0,
            "final_count": 0,
            "excluded_by_ids": 0,
            "excluded_by_companies": 0,
            "effective_days": None,
            "fallback_used": False,
            "error": None,
        }
        try:
            yield metric
        except Exception as e:
            metric["error"] = str(e)[:480]
            raise
        finally:
            metric["latency_ms"] = int((time.monotonic() - started) * 1000)
            self._metrics.append(metric)

    # ─── 공용: Google News RSS 검색 / RSS 피드 fetch ────────────────

    async def _google_news_search(
        self, client: httpx.AsyncClient, keyword: str,
    ) -> List[Dict[str, Any]]:
        """Google News RSS 키워드 검색 (한국어/한국 지역, 키 불필요)."""
        try:
            import feedparser
        except ImportError:
            logger.warning("feedparser 미설치 — 뉴스 검색 비활성")
            return []

        url = (
            "https://news.google.com/rss/search?q="
            + quote(keyword)
            + "&hl=ko&gl=KR&ceid=KR:ko"
        )
        try:
            async def _request():
                response = await client.get(
                    url, timeout=API_TIMEOUT, follow_redirects=True,
                    headers={"User-Agent": _USER_AGENT},
                )
                response.raise_for_status()
                return response.text

            body = await retry_async(_request, max_retries=2, base_delay=1.5)
            feed = await asyncio.to_thread(feedparser.parse, body)
        except Exception as e:
            logger.warning(f"Google News 검색 실패 [{keyword}]: {e}")
            return []

        out: List[Dict[str, Any]] = []
        for entry in (feed.entries or [])[:MAX_PER_KEYWORD]:
            out.append({
                "keyword": keyword,
                "origin": "구글뉴스",
                "title": _strip_html(entry.get("title") or "", max_len=200),
                "url": entry.get("link") or "",
                "published_at": _parse_rss_date(entry),
                "summary": _strip_html(
                    entry.get("summary") or entry.get("description") or ""
                ),
            })
        return out

    async def _fetch_feed(
        self, client: httpx.AsyncClient, label: str, url: str,
    ) -> List[Dict[str, Any]]:
        """등록 RSS 피드 fetch + 파싱 (정책 RSS / 교육 RSS 공용)."""
        try:
            import feedparser
        except ImportError:
            return []

        try:
            async def _request():
                response = await client.get(
                    url, timeout=API_TIMEOUT, follow_redirects=True,
                    headers={"User-Agent": _USER_AGENT},
                )
                response.raise_for_status()
                return response.text

            body = await retry_async(_request, max_retries=2, base_delay=1.5)
            feed = await asyncio.to_thread(feedparser.parse, body)
        except Exception as e:
            logger.warning(f"RSS fetch 실패 [{label}]: {e}")
            return []

        out: List[Dict[str, Any]] = []
        for entry in (feed.entries or [])[:RSS_MAX_ITEMS]:
            out.append({
                "keyword": "",
                "origin": label,
                "title": _strip_html(entry.get("title") or "", max_len=200),
                "url": entry.get("link") or "",
                "published_at": _parse_rss_date(entry),
                "summary": _strip_html(
                    entry.get("summary") or entry.get("description") or ""
                ),
            })
        return out

    async def _search_keywords(
        self, client: httpx.AsyncClient, keywords: List[str],
    ) -> List[Dict[str, Any]]:
        """키워드 병렬 검색 (semaphore 로 동시성 제한)."""
        semaphore = asyncio.Semaphore(3)

        async def _bounded(kw: str):
            async with semaphore:
                return await self._google_news_search(client, kw)

        results = await asyncio.gather(
            *[_bounded(kw) for kw in keywords], return_exceptions=False,
        )
        flat: List[Dict[str, Any]] = []
        for r in results:
            flat.extend(r)
        return flat

    # ─── Source 1: 뉴스 키워드 검색 ─────────────────────────────────

    async def _fetch_news(
        self, client: httpx.AsyncClient,
    ) -> List[Dict[str, Any]]:
        with self._track(
            data_type="news",
            api_path="https://news.google.com/rss/search (news keywords)",
        ) as m:
            raw = await self._search_keywords(client, NEWS_KEYWORDS)
            m["raw_count"] = len(NEWS_KEYWORDS)
            items = [
                _make_item(source="news", category="news", **r) for r in raw
            ]
            m["final_count"] = len(items)
            logger.info(f"뉴스 검색 수집: {len(items)}건")
            return items

    # ─── Source 2: 정책 (RSS + 키워드 검색) ─────────────────────────

    async def _fetch_policy(
        self, client: httpx.AsyncClient,
    ) -> List[Dict[str, Any]]:
        with self._track(
            data_type="policy",
            api_path="korea.kr policy RSS + google news (policy keywords)",
        ) as m:
            feed_tasks = [
                self._fetch_feed(client, label, url)
                for label, url in POLICY_RSS_FEEDS
            ]
            search_task = self._search_keywords(client, POLICY_KEYWORDS)
            results = await asyncio.gather(
                *feed_tasks, search_task, return_exceptions=True,
            )

            items: List[Dict[str, Any]] = []
            # 정책 RSS — 키워드 1차 필터 (전체 보도자료 중 AI/교육 관련만).
            hints = [k for kw in POLICY_KEYWORDS for k in kw.split()] + ["AI", "인공지능"]
            for r in results[:-1]:
                if isinstance(r, Exception):
                    logger.warning(f"정책 RSS 예외: {r}")
                    m["fallback_used"] = True
                    continue
                for raw in r:
                    haystack = raw["title"] + " " + raw["summary"]
                    if any(h in haystack for h in hints):
                        items.append(
                            _make_item(source="policy", category="policy", **raw)
                        )
            # 정책 키워드 뉴스 검색.
            search_result = results[-1]
            if isinstance(search_result, Exception):
                logger.warning(f"정책 키워드 검색 예외: {search_result}")
                m["fallback_used"] = True
            else:
                items.extend(
                    _make_item(source="policy", category="policy", **raw)
                    for raw in search_result
                )

            m["raw_count"] = len(POLICY_RSS_FEEDS) + len(POLICY_KEYWORDS)
            m["final_count"] = len(items)
            logger.info(f"정책 수집: {len(items)}건")
            return items

    # ─── Source 3: 교육·세미나 (키워드 검색 + 선택 RSS) ─────────────

    async def _fetch_courses(
        self, client: httpx.AsyncClient,
    ) -> List[Dict[str, Any]]:
        with self._track(
            data_type="course",
            api_path="google news (course keywords) + course RSS",
        ) as m:
            feed_tasks = [
                self._fetch_feed(client, label, url)
                for label, url in COURSE_RSS_FEEDS
            ]
            search_task = self._search_keywords(client, COURSE_KEYWORDS)
            results = await asyncio.gather(
                *feed_tasks, search_task, return_exceptions=True,
            )

            items: List[Dict[str, Any]] = []
            for r in results:
                if isinstance(r, Exception):
                    logger.warning(f"교육·세미나 수집 예외: {r}")
                    m["fallback_used"] = True
                    continue
                for raw in r:
                    items.append(_make_item(
                        source="course",
                        category=_classify_course(raw["title"]),
                        **raw,
                    ))

            m["raw_count"] = len(COURSE_RSS_FEEDS) + len(COURSE_KEYWORDS)
            m["final_count"] = len(items)
            logger.info(f"교육·세미나 수집: {len(items)}건")
            return items

    # ─── 통합 수집 ─────────────────────────────────────────────────

    async def collect_daily(
        self,
        exclude_ids: Optional[List[int]] = None,  # base interface 호환 (미사용)
        exclude_companies: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """3 sources 병렬 수집 → URL 기준 중복 제거 → 단일 dict.

        Returns:
            {
                "tech_daily": {
                    "report_date": ISO,
                    "news_items": [...],
                    "policy_items": [...],
                    "course_items": [...],
                    "stats": {"news_count": N, "policy_count": N, "course_count": N},
                }
            }
            전부 실패시 빈 dict.
        """
        async with httpx.AsyncClient(timeout=API_TIMEOUT, trust_env=False) as client:
            results = await asyncio.gather(
                self._fetch_news(client),
                self._fetch_policy(client),
                self._fetch_courses(client),
                return_exceptions=True,
            )

        news_items   = results[0] if not isinstance(results[0], Exception) else []
        policy_items = results[1] if not isinstance(results[1], Exception) else []
        course_items = results[2] if not isinstance(results[2], Exception) else []

        for idx, label in enumerate(("뉴스", "정책", "교육·세미나")):
            if isinstance(results[idx], Exception):
                logger.error(f"{label} 수집 예외 — 빈 폴백: {results[idx]}")

        # 소스 간 중복 제거 — 같은 기사가 여러 키워드/소스에서 잡히는 경우.
        # 우선순위: policy > course > news (구체적 카테고리 우선).
        seen: set[str] = set()

        def _dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            kept = []
            for it in items:
                key = (it["url"] or it["title"]).strip().lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                kept.append(it)
            return kept

        policy_items = _dedupe(policy_items)
        course_items = _dedupe(course_items)
        news_items   = _dedupe(news_items)

        if not news_items and not policy_items and not course_items:
            logger.warning("TechBriefing: 3 sources 모두 비어 있음")
            return {}

        now_iso = datetime.now(timezone.utc).isoformat()
        return {
            "tech_daily": {
                "report_date": now_iso,
                "news_items": news_items,
                "policy_items": policy_items,
                "course_items": course_items,
                "stats": {
                    "news_count": len(news_items),
                    "policy_count": len(policy_items),
                    "course_count": len(course_items),
                },
            }
        }
