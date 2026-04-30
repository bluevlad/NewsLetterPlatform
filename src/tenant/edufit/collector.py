"""
EduFit 데이터 수집기
FastAPI API 호출

API Endpoints:
  - GET /reports/daily          → 일일 리포트 (PeriodReportResponse)
  - GET /weekly/summary         → 주간 요약 통계 (WeeklySummary)
  - GET /weekly/ranking         → 주간 강사 랭킹 (List[WeeklyTeacherReport])
  - GET /analysis/summary       → 분석 요약 (AnalysisSummary)
  - GET /analysis/academy-stats → 학원 통계 (List[AcademyStats])
  - GET /academies              → 등록 학원 목록 (List[AcademyResponse])
  - GET /news/recent            → 최근 뉴스 기사 (네이버/구글 뉴스)
  - GET /news/source-stats      → 소스 유형별 언급 통계

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

    async def collect_academies(self) -> list:
        """등록 학원 목록 수집 - GET /academies"""
        try:
            data = await self._get("/academies")
            logger.info(f"EduFit 학원 목록 수집 완료: {len(data) if isinstance(data, list) else 0}건")
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"EduFit 학원 목록 수집 실패: {e}")
            return []

    async def collect_news_articles(self, days: int = 1, limit: int = 10) -> Dict:
        """최근 뉴스 기사 수집 - GET /news/recent"""
        try:
            data = await self._get("/news/recent", {"days": days, "limit": limit})
            articles = data.get("articles", []) if isinstance(data, dict) else []
            logger.info(f"EduFit 뉴스 기사 수집 완료: {len(articles)}건")
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.error(f"EduFit 뉴스 기사 수집 실패: {e}")
            return {}

    async def collect_source_stats(self, days: int = 1) -> Dict:
        """소스 유형별 언급 통계 수집 - GET /news/source-stats"""
        try:
            data = await self._get("/news/source-stats", {"days": days})
            logger.info("EduFit 소스별 통계 수집 완료")
            return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.error(f"EduFit 소스별 통계 수집 실패: {e}")
            return {}

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

        academies = await self.collect_academies()
        if academies:
            result["academies"] = academies

        news = await self.collect_news_articles(days=1, limit=5)
        if news:
            result["news"] = news

        source_stats = await self.collect_source_stats(days=1)
        if source_stats:
            result["source_stats"] = source_stats

        logger.info(f"EduFit 전체 수집 완료: {list(result.keys())}")
        return result

    async def collect_weekly_data(self) -> Dict[str, Any]:
        """주간 요약 뉴스레터용 데이터 수집"""
        result = {}

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

        academies = await self.collect_academies()
        if academies:
            result["academies"] = academies

        news = await self.collect_news_articles(days=7, limit=10)
        if news:
            result["news"] = news

        source_stats = await self.collect_source_stats(days=7)
        if source_stats:
            result["source_stats"] = source_stats

        logger.info(f"EduFit 주간 데이터 수집 완료: {list(result.keys())}")
        return result

    def _generate_service_token(self) -> str:
        """EduFit 인증용 JWT 서비스 토큰 생성"""
        import jwt as pyjwt
        from datetime import datetime, timedelta, timezone

        secret = settings.edufit_jwt_secret
        if not secret:
            logger.warning("EDUFIT_JWT_SECRET이 설정되지 않았습니다.")
            return ""

        payload = {
            "sub": "newsletter-platform@service",
            "email": "newsletter-platform@service",
            "role": "super_admin",
            "auth_type": "service",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            "type": "access",
        }
        return pyjwt.encode(payload, secret, algorithm="HS256")

    async def _get_authenticated(self, path: str, params: dict = None) -> Any:
        """인증이 필요한 API GET 요청"""
        url = f"{self.api_base_url}{path}"
        token = self._generate_service_token()
        if not token:
            raise RuntimeError("서비스 토큰 생성 실패")

        async def _request():
            async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
                response = await client.get(
                    url, params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
                return response.json()

        return await retry_async(_request)

    async def collect_newsletter_html(self, year: int, month: int, days: int = 30) -> str:
        """EduFit 월간 뉴스레터 pre-rendered HTML 수집
        - GET /newsletter/html?year=YYYY&month=MM&days=DD (인증 필요)
        """
        try:
            data = await self._get_authenticated(
                "/newsletter/html",
                params={"year": year, "month": month, "days": days},
            )
            html = data.get("html", "") if isinstance(data, dict) else ""
            if html:
                logger.info(f"EduFit 월간 뉴스레터 HTML 수집 완료: {year}년 {month}월")
            else:
                logger.warning(f"EduFit 월간 뉴스레터 HTML이 비어있습니다: {year}년 {month}월")
            return html
        except Exception as e:
            logger.error(f"EduFit 월간 뉴스레터 HTML 수집 실패: {e}")
            return ""

    async def collect_monthly_data(self) -> Dict[str, Any]:
        """월간 요약 뉴스레터용 데이터 수집 — EduFit newsletter API에서 pre-rendered HTML 사용"""
        from datetime import date as date_cls
        today = date_cls.today()

        html = await self.collect_newsletter_html(today.year, today.month)
        if html:
            return {"prerendered_html": html}

        # fallback: 기존 방식으로 수집
        logger.warning("EduFit 월간 뉴스레터 HTML 수집 실패, 기존 방식으로 fallback")
        result = {}

        analysis_summary = await self.collect_analysis_summary()
        if analysis_summary:
            result["analysis_summary"] = analysis_summary

        academy_stats = await self.collect_academy_stats()
        if academy_stats:
            result["academy_stats"] = academy_stats

        academies = await self.collect_academies()
        if academies:
            result["academies"] = academies

        weekly_summary = await self.collect_weekly_summary()
        if weekly_summary:
            result["weekly_summary"] = weekly_summary

        weekly_ranking = await self.collect_weekly_ranking()
        if weekly_ranking:
            result["weekly_ranking"] = weekly_ranking

        logger.info(f"EduFit 월간 데이터 수집 완료 (fallback): {list(result.keys())}")
        return result
