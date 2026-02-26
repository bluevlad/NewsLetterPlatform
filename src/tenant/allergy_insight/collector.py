"""
AllergyInsight 데이터 수집기
AllergyNewsLetter API 호출

API Endpoint:
  - GET /api/v1/report → 일일 리포트 (Bearer 토큰 인증)
"""

import logging
from typing import Any, Dict

import httpx

from ...common.utils import retry_async
from ...config import settings

logger = logging.getLogger(__name__)

API_TIMEOUT = 60.0  # AI 처리 지연 대비


class AllergyInsightCollector:
    """AllergyInsight API 데이터 수집기"""

    def __init__(self, api_base_url: str = None, api_token: str = None):
        self.api_base_url = (
            api_base_url or settings.allergy_insight_api_url
        ).rstrip("/")
        self.api_token = api_token or settings.allergy_insight_api_token

    async def _get(self, path: str) -> Any:
        """API GET 요청 (3회 재시도, Bearer 토큰 인증)"""
        url = f"{self.api_base_url}{path}"
        headers = {"Authorization": f"Bearer {self.api_token}"}

        async def _request():
            async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                return response.json()

        return await retry_async(_request)

    async def collect_daily_report(self) -> Dict:
        """일일 리포트 수집 - GET /api/v1/report"""
        try:
            data = await self._get("/api/v1/report")
            logger.info("AllergyInsight 일일 리포트 수집 완료")
            return data
        except Exception as e:
            logger.error(f"AllergyInsight 일일 리포트 수집 실패: {e}")
            return {}

    async def collect_all(self) -> Dict[str, Any]:
        """전체 데이터 수집"""
        result = {}

        daily_report = await self.collect_daily_report()
        if daily_report:
            result["daily_report"] = daily_report

        logger.info(f"AllergyInsight 전체 수집 완료: {list(result.keys())}")
        return result
