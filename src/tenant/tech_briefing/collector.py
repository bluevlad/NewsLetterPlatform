"""TechBriefing 데이터 수집기 — SkillRadar 뉴스레터 공급 API 호출.

2026-07 Phase 1: 외부 RSS 직접 수집(SkillRadar 수집 로직 복제)을 SkillRadar
백엔드(9070) REST API 로 전환. 수집·정규화·LLM 요약·큐레이션은 SkillRadar 가
담당하고, 여기서는 그날의 편성 데이터를 받아 기존 tech_daily 아이템 형태로
매핑만 한다. (AllergyInsight 9040 연동과 동일한 "원본 서비스 REST" 패턴)

API:
  - GET {base}/api/v1/newsletter/daily?date=YYYY-MM-DD
    - 인증: X-Newsletter-Key 헤더 (SkillRadar 측 NEWSLETTER_API_KEY 와 동일 값)
    - 편성된 호(Digest)가 있으면 그대로, 없으면 그날 수집분(fetched_at, KST)
      즉석 구성(fallback: true). 빈 결과도 200 + stats 0건.
"""

import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from .config import RECRUITING_HINTS, SEMINAR_HINTS
from ...common.utils import retry_async
from ...config import settings

logger = logging.getLogger(__name__)

API_TIMEOUT = 30.0

# SkillRadar 섹션 → (source, category) 매핑.
# source 축은 기존 3-소스 시절 구분(news|policy|course)을 유지 — formatter/
# 템플릿 호환. seminar 는 course 소스의 하위 카테고리로 취급(기존과 동일).
_SECTION_MAP = {
    "news":    ("news", "news"),
    "policy":  ("policy", "policy"),
    "course":  ("course", "course"),
    "seminar": ("course", "seminar"),
}


def _classify_course(title: str) -> str:
    """교육 항목을 course/seminar 로 분류 (SkillRadar course.py 와 동일 힌트).

    SkillRadar 가 이미 분류해서 내려주므로 수집 경로에서는 미사용 —
    테스트/재분류 유틸로 유지.
    """
    lowered = (title or "").lower()
    if any(h.lower() in lowered for h in SEMINAR_HINTS):
        return "seminar"
    return "course"


def _is_recruiting(title: str) -> bool:
    return any(h in (title or "") for h in RECRUITING_HINTS)


def _map_item(raw: Dict[str, Any], *, source: str, category: str) -> Dict[str, Any]:
    """SkillRadar resource → tech_daily 아이템 매핑.

    SkillRadar 응답 항목: {id, type, title, summary, provider, cost, deadline,
    tags[], audience[], url, published_at}
    """
    title = raw.get("title") or "(제목 없음)"
    url = raw.get("url") or ""
    return {
        "source": source,            # news | policy | course
        "category": category,        # news | policy | course | seminar
        "keyword": "",               # 키워드 검색 경로 아님 — 칩 미표시
        "origin": raw.get("provider") or "SkillRadar",
        "title": title,
        "url": url,
        "published_at": raw.get("published_at"),  # ISO str — formatter 가 파싱
        "summary": (raw.get("summary") or "").strip(),
        # 모집 신호: 제목 힌트 + SkillRadar deadline 메타
        "is_recruiting": _is_recruiting(title) or bool(raw.get("deadline")),
        "dedup_key": f"{category}:{url or title}",
        # SkillRadar 원본 메타 — dedup(Phase 3)/개인화 대비 보존
        "skillradar_id": raw.get("id"),
        "cost": raw.get("cost"),
        "deadline": raw.get("deadline"),
        "tags": raw.get("tags") or [],
        "audience": raw.get("audience") or [],
    }


class TechBriefingCollector:
    """SkillRadar 뉴스레터 공급 API 1회 호출 → tech_daily dict 매핑."""

    def __init__(self, api_base_url: str = None):
        self.api_base_url = (
            api_base_url or settings.skillradar_api_url
        ).rstrip("/")
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

    async def _fetch_daily(self) -> Optional[Dict[str, Any]]:
        """SkillRadar /api/v1/newsletter/daily 호출. 실패 시 None."""
        url = f"{self.api_base_url}/api/v1/newsletter/daily"
        # trust_env=False: AllergyInsight collector 와 동일 — OrbStack 이 주입하는
        # NO_PROXY IPv6 CIDR 이 httpx URL 파서를 깨뜨리는 문제 회피.
        async with httpx.AsyncClient(timeout=API_TIMEOUT, trust_env=False) as client:
            async def _request():
                response = await client.get(
                    url,
                    headers={"X-Newsletter-Key": settings.skillradar_newsletter_key},
                )
                response.raise_for_status()
                return response.json()

            return await retry_async(_request, max_retries=2, base_delay=2.0)

    async def collect_daily(
        self,
        exclude_ids: Optional[List[int]] = None,  # base interface 호환 (미사용)
        exclude_companies: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """SkillRadar 호출 → 섹션 매핑 → 기존 tech_daily 구조 반환.

        Returns:
            {
                "tech_daily": {
                    "report_date": ISO,
                    "news_items": [...],
                    "policy_items": [...],
                    "course_items": [...],   # course + seminar
                    "stats": {"news_count": N, "policy_count": N, "course_count": N},
                    "skillradar": {date, headline, summary, fallback},
                }
            }
            키 미설정/호출 실패/빈 응답 시 빈 dict — 발송 경로가 스킵 처리.
        """
        if not settings.skillradar_newsletter_key:
            logger.warning(
                "TechBriefing: SKILLRADAR_NEWSLETTER_KEY 미설정 — 수집 스킵"
            )
            return {}

        with self._track(
            data_type="skillradar_daily",
            api_path="/api/v1/newsletter/daily",
        ) as m:
            try:
                payload = await self._fetch_daily()
            except Exception as e:
                logger.error(f"SkillRadar 수집 실패: {e}")
                m["error"] = str(e)[:480]
                return {}

            sections = (payload or {}).get("sections") or {}
            m["fallback_used"] = bool((payload or {}).get("fallback"))
            m["raw_count"] = sum(len(v or []) for v in sections.values())

            # 섹션 매핑 + URL/제목 기준 안전 dedup (SkillRadar 가 자연키로
            # 이미 중복 제거하지만, 섹션 간 동일 URL 재등장 방어).
            seen: set[str] = set()
            buckets: Dict[str, List[Dict[str, Any]]] = {
                "news": [], "policy": [], "course": [],
            }
            for section in ("policy", "course", "seminar", "news"):
                source, category = _SECTION_MAP[section]
                for raw in sections.get(section) or []:
                    item = _map_item(raw, source=source, category=category)
                    key = (item["url"] or item["title"]).strip().lower()
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    buckets[source].append(item)

            news_items = buckets["news"]
            policy_items = buckets["policy"]
            course_items = buckets["course"]
            m["final_count"] = len(news_items) + len(policy_items) + len(course_items)

            logger.info(
                "SkillRadar 수집: news %d · policy %d · course %d (fallback=%s)",
                len(news_items), len(policy_items), len(course_items),
                m["fallback_used"],
            )

        if not news_items and not policy_items and not course_items:
            logger.warning("TechBriefing: SkillRadar 응답에 항목 없음")
            return {}

        return {
            "tech_daily": {
                "report_date": datetime.now(timezone.utc).isoformat(),
                "news_items": news_items,
                "policy_items": policy_items,
                "course_items": course_items,
                "stats": {
                    "news_count": len(news_items),
                    "policy_count": len(policy_items),
                    "course_count": len(course_items),
                },
                "skillradar": {
                    "date": (payload or {}).get("date"),
                    "headline": (payload or {}).get("headline"),
                    "summary": (payload or {}).get("summary"),
                    "fallback": bool((payload or {}).get("fallback")),
                },
            }
        }
