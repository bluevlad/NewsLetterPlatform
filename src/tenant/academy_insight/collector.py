"""
AcademyInsight 데이터 수집기
Spring Boot API 호출
"""

import logging
from typing import Any, Dict

import httpx

from ...config import settings

logger = logging.getLogger(__name__)

API_TIMEOUT = 30.0


class AcademyInsightCollector:
    """AcademyInsight API 데이터 수집기"""

    def __init__(self, api_base_url: str = None):
        self.api_base_url = (api_base_url or settings.academy_insight_api_url).rstrip("/")

    async def _get(self, path: str) -> Any:
        """API GET 요청 ({success, data} 엔벨로프 자동 언래핑)"""
        url = f"{self.api_base_url}{path}"
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            response = await client.get(url)
            response.raise_for_status()
            body = response.json()

        if isinstance(body, dict) and "data" in body:
            return body["data"]
        return body

    async def collect_summary(self) -> Dict:
        """요약 통계 수집 - GET /summary"""
        try:
            data = await self._get("/summary")
            logger.info("AcademyInsight 요약 통계 수집 완료")
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.error(f"AcademyInsight 요약 통계 수집 실패: {e}")
            return {}

    async def collect_trending_posts(self) -> list:
        """트렌딩 게시글 수집 - GET /posts/trending"""
        try:
            data = await self._get("/posts/trending")
            logger.info(f"AcademyInsight 트렌딩 게시글 수집 완료: {len(data) if isinstance(data, list) else 0}건")
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"AcademyInsight 트렌딩 게시글 수집 실패: {e}")
            return []

    async def collect_academy_ranking(self) -> list:
        """학원 랭킹 수집 - GET /academies/ranking"""
        try:
            data = await self._get("/academies/ranking")
            logger.info(f"AcademyInsight 학원 랭킹 수집 완료: {len(data) if isinstance(data, list) else 0}건")
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"AcademyInsight 학원 랭킹 수집 실패: {e}")
            return []

    async def collect_all(self) -> Dict[str, Any]:
        """전체 데이터 수집 (개별 에러 처리)"""
        result = {}

        summary = await self.collect_summary()
        if summary:
            result["summary"] = summary

        trending_posts = await self.collect_trending_posts()
        if trending_posts:
            result["trending_posts"] = trending_posts

        academy_ranking = await self.collect_academy_ranking()
        if academy_ranking:
            result["academy_ranking"] = academy_ranking

        logger.info(f"AcademyInsight 전체 수집 완료: {list(result.keys())}")
        return result
