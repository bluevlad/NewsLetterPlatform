"""
AllergyInsight 데이터 수집기
AllergyInsight Backend API v2.0.0 호출

API Endpoints:
  - POST /api/auth/simple/login → JWT 토큰 획득
  - GET  /api/public/analytics/news/recent → 최신 뉴스 (공개, 필터링 적용)
  - GET  /api/admin/news → 뉴스 전체 목록 (Bearer 인증, 주간/월간용)
  - GET  /api/admin/news/stats → 뉴스 통계 (Bearer 인증)
  - GET  /api/papers → 논문 목록 (공개)

Redesign Phase 1 endpoints (NEWSLETTER_REDESIGN_SPEC §3.2):
  - GET  /api/public/analytics/headlines/today
  - GET  /api/public/analytics/company-digest
  - GET  /api/public/drugs/updates
  - GET  /api/public/analytics/weekly-metrics
  Backend 미구현 상태에서 모두 빈 구조로 폴백되어 발송을 깨지 않는다.
"""

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

import httpx

from ...common.utils import retry_async
from ...config import settings

logger = logging.getLogger(__name__)

API_TIMEOUT = 60.0


class AllergyInsightCollector:
    """AllergyInsight API 데이터 수집기 (v2.0.0)"""

    def __init__(self, api_base_url: str = None):
        self.api_base_url = (
            api_base_url or settings.allergy_insight_api_url
        ).rstrip("/")
        self._token: Optional[str] = None

    async def _login(self) -> str:
        """관리자 로그인으로 JWT 토큰 획득"""
        url = f"{self.api_base_url}/api/auth/simple/login"
        payload = {
            "name": settings.allergy_insight_admin_name,
            "phone": settings.allergy_insight_admin_phone,
            "access_pin": settings.allergy_insight_admin_pin,
        }

        async def _request():
            async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                return data["access_token"]

        token = await retry_async(_request)
        self._token = token
        logger.info("AllergyInsight JWT 토큰 획득 완료")
        return token

    async def _get(
        self,
        path: str,
        auth_required: bool = True,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """API GET 요청 (3회 재시도)"""
        url = f"{self.api_base_url}{path}"
        headers = {}
        if auth_required and self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        async def _request():
            async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                return response.json()

        return await retry_async(_request)

    @staticmethod
    def _unwrap(payload: Any) -> Dict[str, Any]:
        """{ data: ..., meta: ... } 래핑된 응답에서 data 본체만 추출. 래핑이 없으면 원본 유지."""
        if isinstance(payload, dict) and "data" in payload:
            inner = payload.get("data")
            return inner if isinstance(inner, dict) else {}
        return payload if isinstance(payload, dict) else {}

    async def _collect_recent_news(
        self, days: int = 1, max_age_days: int = 2, limit: int = 10
    ) -> list[dict]:
        """최신 뉴스 수집 (일일용) - GET /api/public/analytics/news/recent

        Backend에서 is_processed=TRUE, is_relevant=TRUE 필터 및
        published_at DESC 정렬이 적용된 공개 API.
        """
        path = (
            f"/api/public/analytics/news/recent"
            f"?days={days}&max_age_days={max_age_days}&limit={limit}"
        )
        data = await self._get(path, auth_required=False)
        items = data if isinstance(data, list) else data.get("items", [])
        return items

    async def _collect_news(self, page_size: int = 100) -> list[dict]:
        """뉴스 전체 목록 수집 (주간/월간용) - GET /api/admin/news"""
        data = await self._get(f"/api/admin/news?page=1&page_size={page_size}")
        return data.get("items", [])

    async def _collect_news_stats(self) -> dict:
        """뉴스 통계 수집 - GET /api/admin/news/stats"""
        return await self._get("/api/admin/news/stats")

    async def _collect_papers(self, page_size: int = 100) -> list[dict]:
        """논문 목록 수집 - GET /api/papers (공개 API)"""
        data = await self._get(
            f"/api/papers?page=1&page_size={page_size}",
            auth_required=False,
        )
        return data.get("items", [])

    # ─────────────────────────────────────────────────────────────
    # Redesign Phase 1 endpoints (fail-safe: 실패 시 빈 구조 반환)
    # Spec: NEWSLETTER_REDESIGN_SPEC.md §3.2, §4.1
    # ─────────────────────────────────────────────────────────────

    async def _collect_headlines_today(self, limit: int = 5) -> Dict[str, Any]:
        """오늘의 핵심 헤드라인 Top-N 수집 (1기업 1헤드라인).

        Returns:
            {"headlines": [...], "excluded_ids": [int, ...]}
            백엔드 미구현/오류 시 모두 빈 리스트.
        """
        try:
            raw = await self._get(
                "/api/public/analytics/headlines/today",
                auth_required=False,
                params={"limit": limit, "one_per_company": "true"},
            )
            body = self._unwrap(raw)
            headlines = body.get("headlines", []) or []
            excluded_ids = body.get("excluded_ids", []) or []
            logger.info(
                f"AllergyInsight 헤드라인 수집 완료: {len(headlines)}건"
            )
            return {"headlines": headlines, "excluded_ids": excluded_ids}
        except Exception as e:
            logger.warning(
                f"AllergyInsight 헤드라인 수집 실패 (빈 구조 폴백): {e}"
            )
            return {"headlines": [], "excluded_ids": []}

    async def _collect_company_digest(
        self,
        days: int = 7,
        exclude_ids: Optional[list[int]] = None,
    ) -> list[dict]:
        """기업 동향 다이제스트 수집 (headlines에 선정된 기사는 제외).

        Returns:
            [{"company_name", "count_7d", "avg_importance", "representative", "categories"}, ...]
            실패 시 빈 리스트.
        """
        try:
            params: Dict[str, Any] = {"days": days, "limit_per_company": 1}
            if exclude_ids:
                params["exclude_ids"] = ",".join(str(i) for i in exclude_ids)
            raw = await self._get(
                "/api/public/analytics/company-digest",
                auth_required=False,
                params=params,
            )
            body = self._unwrap(raw)
            companies = body.get("companies", []) or []
            logger.info(
                f"AllergyInsight 기업 다이제스트 수집 완료: {len(companies)}건 "
                f"(exclude {len(exclude_ids or [])})"
            )
            return companies
        except Exception as e:
            logger.warning(
                f"AllergyInsight 기업 다이제스트 수집 실패 (빈 리스트 폴백): {e}"
            )
            return []

    async def _collect_drug_updates(self, days: int = 7) -> Dict[str, Any]:
        """약물·처방제 업데이트 수집 (openFDA + MFDS).

        Returns:
            {"new_approvals": [], "label_changes": [], "blackbox_warnings": [],
             "recalls": [], "total": int}
            실패 시 total=0 빈 구조. 템플릿이 total=0 이면 섹션 자동 숨김.
        """
        empty = {
            "new_approvals": [],
            "label_changes": [],
            "blackbox_warnings": [],
            "recalls": [],
            "total": 0,
        }
        try:
            raw = await self._get(
                "/api/public/drugs/updates",
                auth_required=False,
                params={"days": days, "type": "all"},
            )
            body = self._unwrap(raw)
            result = {
                "new_approvals": body.get("new_approvals", []) or [],
                "label_changes": body.get("label_changes", []) or [],
                "blackbox_warnings": body.get("blackbox_warnings", []) or [],
                "recalls": body.get("recalls", []) or [],
            }
            result["total"] = (
                len(result["new_approvals"])
                + len(result["label_changes"])
                + len(result["blackbox_warnings"])
                + len(result["recalls"])
            )
            logger.info(
                f"AllergyInsight 약물 업데이트 수집 완료: total={result['total']}"
            )
            return result
        except Exception as e:
            logger.warning(
                f"AllergyInsight 약물 업데이트 수집 실패 (빈 구조 폴백): {e}"
            )
            return empty

    async def _collect_weekly_metrics(
        self, report_date: Optional[str] = None, window_days: int = 7
    ) -> Dict[str, Any]:
        """주간 브리핑 메트릭 수집 (월요일 daily 또는 weekly/monthly 발송에 포함).

        Returns:
            {"period": {...}, "category_trend_7d": [...], "top_keywords": [...],
             "top_companies": [...], "drug_updates_summary": {...}}
            실패 시 빈 dict. 템플릿이 falsy 체크로 섹션 숨김.
        """
        try:
            params: Dict[str, Any] = {"window_days": window_days}
            if report_date:
                params["report_date"] = report_date
            raw = await self._get(
                "/api/public/analytics/weekly-metrics",
                auth_required=False,
                params=params,
            )
            body = self._unwrap(raw)
            logger.info("AllergyInsight 주간 메트릭 수집 완료")
            return body or {}
        except Exception as e:
            logger.warning(
                f"AllergyInsight 주간 메트릭 수집 실패 (빈 dict 폴백): {e}"
            )
            return {}

    def _transform_news(self, raw_items: list[dict]) -> list[dict]:
        """v2.0.0 뉴스 아이템 → daily_report top_news 포맷 변환"""
        result = []
        for item in raw_items:
            result.append({
                "id": item.get("id"),
                "content_type": "뉴스",
                "title": item.get("title", ""),
                "description": item.get("description", ""),
                "link": item.get("url", ""),
                "original_link": item.get("url", ""),
                "pub_date": item.get("published_at", ""),
                "source": item.get("source", ""),
                "keyword": item.get("search_keyword", ""),
                "category": item.get("category") or "기타",
                "summary": item.get("summary"),
                "importance_score": item.get("importance_score"),
                "company": item.get("company_name"),
            })
        return result

    def _transform_papers(self, raw_items: list[dict]) -> list[dict]:
        """v2.0.0 논문 아이템 → daily_report papers 포맷 변환"""
        result = []
        for item in raw_items:
            result.append({
                "id": item.get("id"),
                "content_type": "논문",
                "title": item.get("title", ""),
                "link": item.get("url", ""),
                "journal": item.get("journal", ""),
                "pmid": item.get("pmid"),
                "doi": item.get("doi"),
                "authors": item.get("authors", ""),
                "pub_date": str(item.get("year", "")),
                "abstract": item.get("abstract", ""),
            })
        return result

    def _build_news_groups(self, news_items: list[dict]) -> list[dict]:
        """뉴스를 카테고리별로 그룹핑"""
        from collections import defaultdict

        by_category = defaultdict(list)
        for item in news_items:
            cat = item.get("category") or "기타"
            by_category[cat].append(item)

        category_config = {
            "임상/치료": {"icon": "🏥", "color": "#2e7d32", "bg_color": "#e8f5e9"},
            "연구/학술": {"icon": "🔬", "color": "#1565c0", "bg_color": "#e3f2fd"},
            "생활/관리": {"icon": "🏠", "color": "#ef6c00", "bg_color": "#fff3e0"},
            "산업/규제": {"icon": "🏢", "color": "#6a1b9a", "bg_color": "#f3e5f5"},
            "기타": {"icon": "📰", "color": "#757575", "bg_color": "#fafafa"},
        }

        groups = []
        for cat, items in by_category.items():
            cfg = category_config.get(cat, category_config["기타"])
            groups.append({
                "title": cat,
                "icon": cfg["icon"],
                "color": cfg["color"],
                "border_color": cfg["color"],
                "bg_color": cfg["bg_color"],
                "entries": [
                    {"article": item, "category_name": cat}
                    for item in items
                ],
                "total_count": len(items),
            })
        return groups

    def _build_company_news(self, news_items: list[dict]) -> list[dict]:
        """뉴스를 기업별로 그룹핑"""
        from collections import defaultdict

        by_company = defaultdict(list)
        for item in news_items:
            company = item.get("company")
            if company:
                by_company[company].append(item)

        result = []
        for name, items in by_company.items():
            result.append({
                "name": name,
                "type": "main",
                "trend_summary": "",
                "articles": [
                    {
                        "title": item["title"],
                        "link": item["link"],
                        "source": item.get("source", ""),
                        "pub_date": item.get("pub_date", ""),
                    }
                    for item in items
                ],
            })
        return result

    async def collect_daily_report(self) -> Dict:
        """일일 리포트 수집.

        Phase 1 전환: 기존 경로(top_news/company_news/news_groups/papers)는 유지하고,
        신규 재구성 섹션(top_headlines/company_digest/drug_updates/weekly_metrics)을
        병렬로 추가 수집한다. 백엔드 신규 API 미구현 구간에는 모두 빈 구조로 폴백되므로
        기존 렌더 경로만으로도 발송이 정상 동작한다.
        """
        try:
            # 1. 최신 뉴스 수집 (공개 API — 인증 불필요)
            #    Backend에서 is_processed, is_relevant 필터 +
            #    published_at DESC 정렬 + max_age_days 적용
            raw_recent_news = await self._collect_recent_news(
                days=1, max_age_days=2, limit=10
            )

            # 2. 논문 수집 (공개 API — 인증 불필요)
            raw_papers = await self._collect_papers()

            # 3. 통계 수집 (인증 필요 — 실패 시 기본값 사용)
            raw_stats = {}
            try:
                await self._login()
                raw_stats = await self._collect_news_stats()
            except Exception as e:
                logger.warning(f"뉴스 통계 수집 실패 (기본값 사용): {e}")

            # 4. Redesign Phase 1: 신규 섹션 데이터 수집 (실패해도 무시)
            headlines_payload = await self._collect_headlines_today(limit=5)
            excluded_ids = headlines_payload.get("excluded_ids", [])
            company_digest = await self._collect_company_digest(
                days=7, exclude_ids=excluded_ids
            )
            drug_updates = await self._collect_drug_updates(days=7)

            # 주간 메트릭은 월요일 daily 에만 포함 (spec §4.6 옵션 A)
            weekly_metrics: Dict[str, Any] = {}
            if date.today().weekday() == 0:
                weekly_metrics = await self._collect_weekly_metrics()

            # 5. 기존 포맷 변환 (Phase 1 동안 유지)
            news_items = self._transform_news(raw_recent_news)
            paper_items = self._transform_papers(raw_papers)

            # API 응답이 이미 published_at DESC + importance 2차 정렬이므로
            # 순서를 그대로 유지
            top_news = news_items

            # 뉴스 그룹 (카테고리별)
            news_groups = self._build_news_groups(news_items)

            # 기업 뉴스
            company_news = self._build_company_news(news_items)

            now = datetime.now(timezone.utc).isoformat()

            report = {
                "report_date": now,
                "generated_at": now,
                # 기존 Phase 0 경로 (유지)
                "top_news": top_news,
                "news_groups": news_groups,
                "papers": paper_items[:20],
                "company_news": company_news,
                # 신규 Phase 1 경로 (백엔드 준비 후 실제 렌더됨)
                "top_headlines": headlines_payload.get("headlines", []),
                "headlines_excluded_ids": excluded_ids,
                "company_digest": company_digest,
                "drug_updates": drug_updates,
                "weekly_metrics": weekly_metrics,
                "stats": {
                    "news_count": raw_stats.get("total_news", len(news_items)),
                    "paper_count": len(paper_items),
                    "company_count": len(company_news),
                    "drug_count": drug_updates.get("total", 0),
                    "total_count": (
                        raw_stats.get("total_news", len(news_items))
                        + len(paper_items)
                        + len(company_news)
                    ),
                    "trend_company_count": len(company_news),
                },
            }

            logger.info(
                f"AllergyInsight 일일 리포트 수집 완료: "
                f"최신 뉴스 {len(news_items)}건, 논문 {len(paper_items)}건, "
                f"기업 {len(company_news)}건, "
                f"헤드라인 {len(report['top_headlines'])}건, "
                f"기업다이제스트 {len(company_digest)}건, "
                f"약물 {drug_updates.get('total', 0)}건"
            )
            return report

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
