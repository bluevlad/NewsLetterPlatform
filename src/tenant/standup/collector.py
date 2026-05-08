"""StandUp Insight 뉴스레터용 데이터 수집기.

원본 서비스: StandUp (port 9060) / `/api/v1/insight/*`.
- GET /insight/newsletters — 최근 합성된 weekly 뉴스레터 메타 (KPI 포함)
- GET /insight/events — 7일치 ingestion 이벤트 풀
"""

import logging
from datetime import datetime, timedelta, timezone, date as date_t
from typing import Any, Dict, List, Optional

import httpx

from ...common.utils import retry_async
from ...config import settings

logger = logging.getLogger(__name__)

API_TIMEOUT = 30.0


class StandUpCollector:
    """StandUp Insight API 수집기."""

    def __init__(self, api_base_url: Optional[str] = None):
        self.api_base_url = (
            api_base_url or settings.standup_api_url
        ).rstrip("/")

    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.api_base_url}{path}"

        async def _request():
            async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()

        return await retry_async(_request)

    async def _list_newsletters(self, limit: int = 5) -> List[Dict[str, Any]]:
        try:
            data = await self._get(
                "/api/v1/insight/newsletters", params={"limit": limit}
            )
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"StandUp /insight/newsletters 실패: {e}")
            return []

    async def _list_events(
        self,
        days: int = 7,
        limit: int = 200,
        source_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        try:
            params: Dict[str, Any] = {"days": days, "limit": limit}
            if source_type:
                params["source_type"] = source_type
            data = await self._get("/api/v1/insight/events", params=params)
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning(f"StandUp /insight/events 실패: {e}")
            return []

    @staticmethod
    def _parse_dt(value: Optional[str]) -> Optional[str]:
        """ISO 문자열 그대로 패스 (포매터에서 datetime 변환)."""
        return value

    async def collect_weekly(
        self,
        date_from: Optional[date_t] = None,
        date_to: Optional[date_t] = None,
    ) -> Dict[str, Any]:
        """주간 인사이트 데이터 수집.

        Returns:
            {
                "weekly_insight": {
                    "period_start": ISO date,
                    "period_end": ISO date,
                    "headline": str | None,
                    "subject": str | None,
                    "source_newsletter_id": str | None,
                    "kpis": {...},
                    "events": [...],
                    "events_total": int,
                    "generated_at": ISO datetime,
                }
            }
            실패 시 빈 dict (스케줄러가 발송 스킵).
        """
        # 1) 가장 최근 합성된 weekly 뉴스레터 메타 (KPI/headline 출처).
        newsletters = await self._list_newsletters(limit=5)
        latest = newsletters[0] if newsletters else None

        # 2) 최근 7일 events.
        events_raw = await self._list_events(days=7, limit=200)

        # period_start / period_end 결정 (latest 우선, 없으면 date_from/to 또는 7일 윈도).
        if latest:
            period_start = latest.get("period_start")
            period_end = latest.get("period_end")
        else:
            today = date_t.today()
            period_start = (date_from or today - timedelta(days=6)).isoformat()
            period_end = (date_to or today).isoformat()

        if not events_raw and not latest:
            logger.warning("StandUp weekly: 뉴스레터/이벤트 모두 비어 있음")
            return {}

        result = {
            "weekly_insight": {
                "period_start": period_start,
                "period_end": period_end,
                "headline": (latest or {}).get("headline"),
                "subject": (latest or {}).get("subject"),
                "source_newsletter_id": (latest or {}).get("id"),
                "kpis": (latest or {}).get("kpis") or {},
                "events": events_raw,
                "events_total": len(events_raw),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
        }
        logger.info(
            f"StandUp weekly 수집 완료: "
            f"period={period_start}~{period_end}, "
            f"events={len(events_raw)}, "
            f"latest_newsletter_id={(latest or {}).get('id')}"
        )
        return result
