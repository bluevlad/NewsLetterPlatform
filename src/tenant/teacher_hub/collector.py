"""
TeacherHub 데이터 수집기
Spring Boot API 호출
"""

import logging
from typing import Any, Dict

import httpx

from ...config import settings

logger = logging.getLogger(__name__)

API_TIMEOUT = 30.0


class TeacherHubCollector:
    """TeacherHub API 데이터 수집기"""

    def __init__(self, api_base_url: str = None):
        self.api_base_url = (api_base_url or settings.teacherhub_api_url).rstrip("/")

    async def _get(self, path: str) -> Any:
        """API GET 요청"""
        url = f"{self.api_base_url}{path}"
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()

    async def collect_daily_report(self) -> Dict:
        """일일 리포트 수집 - GET /reports/daily"""
        try:
            data = await self._get("/reports/daily")
            logger.info(f"TeacherHub 일일 리포트 수집 완료")
            return data
        except Exception as e:
            logger.error(f"TeacherHub 일일 리포트 수집 실패: {e}")
            return {}

    async def collect_ranking(self) -> list:
        """강사 랭킹 수집 - GET /analysis/ranking"""
        try:
            data = await self._get("/analysis/ranking")
            logger.info(f"TeacherHub 랭킹 수집 완료: {len(data) if isinstance(data, list) else 0}건")
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"TeacherHub 랭킹 수집 실패: {e}")
            return []

    async def collect_summary(self) -> Dict:
        """요약 통계 수집 - GET /analysis/summary"""
        try:
            data = await self._get("/analysis/summary")
            logger.info(f"TeacherHub 요약 통계 수집 완료")
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.error(f"TeacherHub 요약 통계 수집 실패: {e}")
            return {}

    async def collect_all(self) -> Dict[str, Any]:
        """전체 데이터 수집 (개별 에러 처리)"""
        result = {}

        daily_report = await self.collect_daily_report()
        if daily_report:
            result["daily_report"] = daily_report

        ranking = await self.collect_ranking()
        if ranking:
            result["ranking"] = ranking

        summary = await self.collect_summary()
        if summary:
            result["summary"] = summary

        logger.info(f"TeacherHub 전체 수집 완료: {list(result.keys())}")
        return result
