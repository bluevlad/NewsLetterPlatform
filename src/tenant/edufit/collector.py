"""
EduFit 데이터 수집기
FastAPI API 호출

API Endpoints:
  - GET /reports/daily          → 일일 리포트 (PeriodReportResponse)
  - GET /weekly/summary         → 주간 요약 통계 (WeeklySummary)
  - GET /weekly/ranking         → 주간 강사 랭킹 (List[WeeklyTeacherReport])
  - GET /analysis/summary       → 분석 요약 (AnalysisSummary)
  - GET /analysis/academy-stats → 학원 통계 (List[AcademyStats])

Note: /weekly/current 엔드포인트 없음 → ISO week 직접 계산
"""

import logging
from datetime import date
from typing import Any, Dict

import httpx

from ...common.utils import retry_async
from ...config import settings

logger = logging.getLogger(__name__)

API_TIMEOUT = 30.0


class EduFitCollector:
    """EduFit API 데이터 수집기"""

    def __init__(self, api_base_url: str = None):
        self.api_base_url = (api_base_url or settings.edufit_api_url).rstrip("/")

    async def _get(self, path: str, params: dict = None) -> Any:
        """API GET 요청 (3회 재시도)"""
        url = f"{self.api_base_url}{path}"

        async def _request():
            async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response.json()

        return await retry_async(_request)

    def _get_current_week(self) -> Dict[str, int]:
        """현재 ISO 주차 계산 (/weekly/current 엔드포인트 없음)"""
        today = date.today()
        iso = today.isocalendar()
        return {"year": iso[0], "week": iso[1]}

    async def collect_daily_report(self) -> Dict:
        """일일 리포트 수집 - GET /reports/daily"""
        try:
            data = await self._get("/reports/daily")
            logger.info("EduFit 일일 리포트 수집 완료")
            return data
        except Exception as e:
            logger.error(f"EduFit 일일 리포트 수집 실패: {e}")
            return {}

    async def collect_weekly_summary(self) -> Dict:
        """주간 요약 통계 수집 - GET /weekly/summary"""
        try:
            current = self._get_current_week()
            year = current["year"]
            week = current["week"]

            data = await self._get("/weekly/summary", {"year": year, "week": week})

            # 현재 주차 데이터가 없으면 직전 주차 조회
            if data.get("totalMentions", 0) == 0 and week > 1:
                prev_data = await self._get("/weekly/summary", {"year": year, "week": week - 1})
                if prev_data.get("totalMentions", 0) > 0:
                    data = prev_data

            logger.info(f"EduFit 주간 요약 수집 완료: {year}년 {week}주차")
            return data
        except Exception as e:
            logger.error(f"EduFit 주간 요약 수집 실패: {e}")
            return {}

    async def collect_weekly_ranking(self) -> list:
        """주간 강사 랭킹 수집 - GET /weekly/ranking"""
        try:
            current = self._get_current_week()
            year = current["year"]
            week = current["week"]

            data = await self._get("/weekly/ranking", {"year": year, "week": week, "limit": 10})

            # 현재 주차 데이터가 없으면 직전 주차 조회
            if not data and week > 1:
                data = await self._get("/weekly/ranking", {"year": year, "week": week - 1, "limit": 10})

            logger.info(f"EduFit 주간 랭킹 수집 완료: {len(data) if isinstance(data, list) else 0}건")
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"EduFit 주간 랭킹 수집 실패: {e}")
            return []

    async def collect_analysis_summary(self) -> Dict:
        """분석 요약 수집 - GET /analysis/summary"""
        try:
            data = await self._get("/analysis/summary")
            logger.info("EduFit 분석 요약 수집 완료")
            return data
        except Exception as e:
            logger.error(f"EduFit 분석 요약 수집 실패: {e}")
            return {}

    async def collect_academy_stats(self) -> list:
        """학원 통계 수집 - GET /analysis/academy-stats"""
        try:
            data = await self._get("/analysis/academy-stats")
            logger.info(f"EduFit 학원 통계 수집 완료: {len(data) if isinstance(data, list) else 0}건")
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"EduFit 학원 통계 수집 실패: {e}")
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

        analysis_summary = await self.collect_analysis_summary()
        if analysis_summary:
            result["analysis_summary"] = analysis_summary

        academy_stats = await self.collect_academy_stats()
        if academy_stats:
            result["academy_stats"] = academy_stats

        logger.info(f"EduFit 전체 수집 완료: {list(result.keys())}")
        return result
