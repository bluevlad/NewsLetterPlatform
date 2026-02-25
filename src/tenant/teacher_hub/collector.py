"""
TeacherHub 데이터 수집기
Spring Boot API 호출

API Endpoints:
  - GET /reports/daily          → 일일 리포트 (PeriodReportDTO)
  - GET /weekly/summary         → 주간 요약 통계 (WeeklySummaryDTO)
  - GET /weekly/ranking         → 주간 강사 랭킹 (List<WeeklyReportDTO>)
  - GET /weekly/current         → 현재 주차 정보
  - GET /analysis/academy-stats → 학원 통계 (AcademyInsight 통합)
"""

import logging
from typing import Any, Dict

import httpx

from ...common.utils import retry_async
from ...config import settings

logger = logging.getLogger(__name__)

API_TIMEOUT = 30.0


class TeacherHubCollector:
    """TeacherHub API 데이터 수집기"""

    def __init__(self, api_base_url: str = None):
        self.api_base_url = (api_base_url or settings.teacherhub_api_url).rstrip("/")

    async def _get(self, path: str, params: dict = None) -> Any:
        """API GET 요청 (3회 재시도)"""
        url = f"{self.api_base_url}{path}"

        async def _request():
            async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()

        return await retry_async(_request)

    async def collect_daily_report(self) -> Dict:
        """일일 리포트 수집 - GET /reports/daily"""
        try:
            data = await self._get("/reports/daily")
            logger.info("TeacherHub 일일 리포트 수집 완료")
            return data
        except Exception as e:
            logger.error(f"TeacherHub 일일 리포트 수집 실패: {e}")
            return {}

    async def _get_current_week(self) -> Dict:
        """현재 주차 정보 조회 - GET /weekly/current"""
        try:
            return await self._get("/weekly/current")
        except Exception as e:
            logger.error(f"TeacherHub 현재 주차 조회 실패: {e}")
            return {}

    async def collect_weekly_summary(self) -> Dict:
        """주간 요약 통계 수집 - GET /weekly/summary"""
        try:
            current = await self._get_current_week()
            if not current:
                return {}

            year = current.get("year")
            week = current.get("week")

            data = await self._get("/weekly/summary", {"year": year, "week": week})

            # 현재 주차 데이터가 없으면 직전 주차 조회
            if data.get("totalMentions", 0) == 0 and week and week > 1:
                prev_data = await self._get("/weekly/summary", {"year": year, "week": week - 1})
                if prev_data.get("totalMentions", 0) > 0:
                    data = prev_data

            logger.info(f"TeacherHub 주간 요약 수집 완료: {data.get('weekLabel', '')}")
            return data
        except Exception as e:
            logger.error(f"TeacherHub 주간 요약 수집 실패: {e}")
            return {}

    async def collect_weekly_ranking(self) -> list:
        """주간 강사 랭킹 수집 - GET /weekly/ranking"""
        try:
            current = await self._get_current_week()
            if not current:
                return []

            year = current.get("year")
            week = current.get("week")

            data = await self._get("/weekly/ranking", {"year": year, "week": week, "limit": 10})

            # 현재 주차 데이터가 없으면 직전 주차 조회
            if not data and week and week > 1:
                data = await self._get("/weekly/ranking", {"year": year, "week": week - 1, "limit": 10})

            logger.info(f"TeacherHub 주간 랭킹 수집 완료: {len(data) if isinstance(data, list) else 0}건")
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"TeacherHub 주간 랭킹 수집 실패: {e}")
            return []

    async def collect_academy_stats(self) -> list:
        """학원 통계 수집 - GET /analysis/academy-stats"""
        try:
            data = await self._get("/analysis/academy-stats")
            # {success, data} 엔벨로프 자동 언래핑
            if isinstance(data, dict) and "data" in data:
                data = data["data"]
            logger.info(f"TeacherHub 학원 통계 수집 완료: {len(data) if isinstance(data, list) else 0}건")
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"TeacherHub 학원 통계 수집 실패: {e}")
            return []

    async def collect_all(self) -> Dict[str, Any]:
        """전체 데이터 수집 (개별 에러 처리)"""
        result = {}

        daily_report = await self.collect_daily_report()
        if daily_report:
            result["daily_report"] = daily_report

        weekly_summary = await self.collect_weekly_summary()
        if weekly_summary:
            result["weekly_summary"] = weekly_summary

        weekly_ranking = await self.collect_weekly_ranking()
        if weekly_ranking:
            result["weekly_ranking"] = weekly_ranking

        academy_stats = await self.collect_academy_stats()
        if academy_stats:
            result["academy_stats"] = academy_stats

        logger.info(f"TeacherHub 전체 수집 완료: {list(result.keys())}")
        return result
