"""
AllergyInsight 데이터 수집기
AllergyInsight Backend API v2.0.0 호출

API Endpoints:
  - POST /api/auth/simple/login → JWT 토큰 획득
  - GET  /api/admin/news → 뉴스 전체 목록 (Bearer 인증, 주간/월간용)
  - GET  /api/admin/news/stats → 뉴스 통계 (Bearer 인증)
  - GET  /api/papers → 논문 목록 (공개)

Redesign Phase 1 endpoints (NEWSLETTER_REDESIGN_SPEC §3.2):
  - GET  /api/public/analytics/headlines/today → 핵심 헤드라인 Top-N (P1 구현 완료)
  - GET  /api/public/analytics/company-digest → 기업 동향 다이제스트 (P1 구현 완료)
  - GET  /api/public/drugs/updates → 약물 업데이트 (P2 예정)
  - GET  /api/public/analytics/weekly-metrics → 주간 메트릭 (P3 예정)
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

    async def _post(
        self,
        path: str,
        auth_required: bool = True,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """API POST 요청 (3회 재시도). 대량 exclude_ids 전달 등 GET 길이 한계 대응용."""
        url = f"{self.api_base_url}{path}"
        headers = {}
        if auth_required and self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        async def _request():
            async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
                response = await client.post(url, headers=headers, json=json_body)
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

    # GET URL 대략 안전선 — 1800 bytes 초과 예상 시 POST alias 로 전환.
    _GET_EXCLUDE_IDS_BUDGET = 1800

    async def _collect_headlines_today(
        self,
        limit: int = 5,
        exclude_ids: Optional[list[int]] = None,
    ) -> Dict[str, Any]:
        """오늘의 핵심 헤드라인 Top-N 수집 (1기업 1헤드라인).

        exclude_ids: 최근 발송 이력. 풀에서 원천 제외하여 반복 발송 차단.
        대량(약 200+) 전달 시 GET URL 길이 한계 대응으로 POST alias 로 자동 전환.

        Returns:
            {"headlines": [...], "excluded_ids": [int, ...]}
            백엔드 미구현/오류 시 모두 빈 리스트.
        """
        exclude_ids = [int(i) for i in (exclude_ids or [])]
        exclude_csv = ",".join(str(i) for i in exclude_ids)
        try:
            if exclude_csv and len(exclude_csv) > self._GET_EXCLUDE_IDS_BUDGET:
                logger.info(
                    f"AllergyInsight 헤드라인: exclude_ids={len(exclude_ids)}건 → POST alias 사용"
                )
                raw = await self._post(
                    "/api/public/analytics/headlines/today:select",
                    auth_required=False,
                    json_body={
                        "limit": limit,
                        "one_per_company": True,
                        "fallback_days": [1, 2],
                        "exclude_ids": exclude_ids,
                    },
                )
            else:
                params: Dict[str, Any] = {
                    "limit": limit,
                    "one_per_company": "true",
                    "fallback_days": "1,2",
                }
                if exclude_csv:
                    params["exclude_ids"] = exclude_csv
                raw = await self._get(
                    "/api/public/analytics/headlines/today",
                    auth_required=False,
                    params=params,
                )
            body = self._unwrap(raw)
            headlines = body.get("headlines", []) or []
            excluded_ids = body.get("excluded_ids", []) or []
            logger.info(
                f"AllergyInsight 헤드라인 수집 완료: {len(headlines)}건 "
                f"(exclude {len(exclude_ids)})"
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
        exclude_companies: Optional[list[str]] = None,
        target_count: int = 5,
    ) -> list[dict]:
        """기업 동향 다이제스트 수집 (headlines에 선정된 기사는 제외).

        백엔드는 days=7 누적만 제공하므로, 동일 기업이 daily 마다 반복
        노출되는 문제를 클라이언트 사이드에서 보완한다:
          1) days=7 1차 호출
          2) exclude_companies (최근 발송 기업) + 카테고리 다양성 강제 필터
          3) 결과가 target_count 미만이면 days=14 로 풀 확장 후 재시도

        Returns:
            [{"company_name", "count_7d", "avg_importance", "representative",
              "categories"}, ...] — 최대 target_count 건.
            실패 시 빈 리스트.
        """
        excluded_companies = {
            (c or "").strip() for c in (exclude_companies or []) if c
        }

        async def _fetch(window_days: int) -> list[dict]:
            params: Dict[str, Any] = {
                "days": window_days,
                "limit_per_company": 1,
                # 백엔드 default 20 → 클라이언트 필터 여유분 확보를 위해 더 넓게
                "max_companies": 40,
            }
            if exclude_ids:
                params["exclude_ids"] = ",".join(str(i) for i in exclude_ids)
            raw = await self._get(
                "/api/public/analytics/company-digest",
                auth_required=False,
                params=params,
            )
            body = self._unwrap(raw)
            return body.get("companies", []) or []

        def _apply_filters(
            companies: list[dict], max_per_category: int = 3
        ) -> list[dict]:
            """기업명 dedup + 카테고리 다양성 강제."""
            seen_categories: dict[str, int] = {}
            kept: list[dict] = []
            for c in companies:
                name = (c.get("company_name") or "").strip()
                if not name or name in excluded_companies:
                    continue
                cats = c.get("categories") or []
                primary_cat = cats[0] if cats else "etc"
                if seen_categories.get(primary_cat, 0) >= max_per_category:
                    continue
                kept.append(c)
                seen_categories[primary_cat] = (
                    seen_categories.get(primary_cat, 0) + 1
                )
                if len(kept) >= target_count:
                    break
            return kept

        try:
            companies_7d = await _fetch(7)
            filtered = _apply_filters(companies_7d)
            if len(filtered) < target_count:
                # 풀이 부족하면 14일로 확장 후 한 번 더 시도
                companies_14d = await _fetch(14)
                seen_names = {c.get("company_name") for c in filtered}
                extra_pool = [
                    c for c in companies_14d
                    if c.get("company_name") not in seen_names
                ]
                extra_filtered = _apply_filters(
                    extra_pool, max_per_category=3
                )
                filtered.extend(
                    extra_filtered[: target_count - len(filtered)]
                )
            logger.info(
                f"AllergyInsight 기업 다이제스트 수집 완료: "
                f"raw={len(companies_7d)} → filtered={len(filtered)} "
                f"(exclude_ids={len(exclude_ids or [])}, "
                f"exclude_companies={len(excluded_companies)})"
            )
            return filtered
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

    # PaperAllergenLink.link_type → (한글 라벨, 색상) 매핑.
    # 백엔드 paper_link_extractor.py 기준 link_type 카탈로그.
    _PAPER_LINK_TYPE_META = {
        "symptom":   ("증상",   "#c62828"),
        "dietary":   ("식이",   "#ef6c00"),
        "mechanism": ("기전",   "#6a1b9a"),
        "diagnosis": ("진단",   "#1565c0"),
        "treatment": ("치료",   "#2e7d32"),
        "prevention":("예방",   "#00838f"),
        "epidemiology": ("역학", "#5e35b1"),
        "general":   ("일반",   "#546e7a"),
    }

    def _transform_papers(self, raw_items: list[dict]) -> list[dict]:
        """v2.0.0 논문 아이템 → daily_report papers 포맷 변환.

        백엔드 PaperResponse 가 이미 포함하는 allergen_links / keywords 를
        활용해 알러지 도메인 강조용 메타를 부착한다:
          - allergen_tags: 칩으로 표시 (link_type 라벨/색상 + relevance_score)
          - primary_allergen: relevance_score 최고 1건
          - specific_items: 한글 키워드 묶음 (중복 제거)
          - keywords: PaperResponse.keywords (Phase 1 검색 결과 영속화 필드)
        정렬 키도 published year → primary_allergen.relevance_score 우선으로 변경.
        """
        meta = self._PAPER_LINK_TYPE_META
        transformed: list[dict] = []
        for item in raw_items:
            raw_links = item.get("allergen_links") or []
            allergen_tags: list[dict] = []
            specific_items: list[str] = []
            for link in raw_links:
                link_type = (link.get("link_type") or "general").lower()
                label, color = meta.get(link_type, meta["general"])
                tag = {
                    "allergen_code": link.get("allergen_code"),
                    "link_type": link_type,
                    "link_type_label": label,
                    "color": color,
                    "relevance_score": int(link.get("relevance_score") or 0),
                    "specific_item": link.get("specific_item"),
                }
                allergen_tags.append(tag)
                spec = link.get("specific_item")
                if spec and spec not in specific_items:
                    specific_items.append(spec)
            allergen_tags.sort(
                key=lambda t: t["relevance_score"], reverse=True
            )
            primary = allergen_tags[0] if allergen_tags else None

            keywords = item.get("keywords") or []
            if isinstance(keywords, str):
                keywords = [k.strip() for k in keywords.split(",") if k.strip()]

            transformed.append({
                "id": item.get("id"),
                "content_type": "논문",
                "title": item.get("title", ""),
                "title_kr": item.get("title_kr"),
                "link": item.get("url", ""),
                "journal": item.get("journal", ""),
                "pmid": item.get("pmid"),
                "doi": item.get("doi"),
                "authors": item.get("authors", ""),
                "pub_date": str(item.get("year", "")),
                "abstract": item.get("abstract", ""),
                "paper_type": item.get("paper_type"),
                # ▼ N1 신규 필드
                "allergen_tags": allergen_tags[:5],
                "primary_allergen": primary,
                "primary_relevance": (
                    primary["relevance_score"] if primary else 0
                ),
                "specific_items": specific_items[:5],
                "keywords": keywords[:8],
            })

        # primary relevance 우선, 그 다음 발행 연도로 정렬
        transformed.sort(
            key=lambda p: (
                p.get("primary_relevance") or 0,
                p.get("pub_date") or "",
            ),
            reverse=True,
        )
        return transformed

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

    async def collect_daily_report(
        self,
        exclude_ids: Optional[list[int]] = None,
        exclude_companies: Optional[list[str]] = None,
    ) -> Dict:
        """일일 리포트 수집.

        Phase 1 전환 완료: 신규 재구성 섹션(top_headlines/company_digest)을 주 경로로 사용.
        Spec: NEWSLETTER_REDESIGN_SPEC §4.2

        Args:
            exclude_ids: NewsLetterPlatform `sent_articles` 최근 N일 기사 ID.
                헤드라인·company_digest 양쪽에서 풀 단계에서 원천 제외.
            exclude_companies: 최근 N일 발송된 기업명. company_digest 의 일 단위
                반복 노출(같은 기업이 7일 풀에서 매일 재선정) 방지용 클라이언트 필터.
        """
        recent_excl = list(exclude_ids or [])
        recent_companies = list(exclude_companies or [])
        try:
            # 1. 핵심 헤드라인 수집 (공개 API, 1기업 1헤드라인)
            headlines_payload = await self._collect_headlines_today(
                limit=5, exclude_ids=recent_excl
            )
            top_headlines = headlines_payload.get("headlines", [])
            excluded_ids = headlines_payload.get("excluded_ids", [])

            # 2. 기업 동향 다이제스트
            #   - 풀에서 헤드라인 선정분 + 최근 발송 기사 ID 제외
            #   - 결과에서 최근 발송 기업 + 오늘 헤드라인에 이미 잡힌 기업 dedup
            digest_excl = sorted({*excluded_ids, *recent_excl})
            today_headline_companies = [
                (h.get("company_name") or "").strip()
                for h in top_headlines
                if h.get("company_name")
            ]
            digest_company_excl = sorted({
                *recent_companies,
                *today_headline_companies,
            })
            company_digest = await self._collect_company_digest(
                days=7,
                exclude_ids=digest_excl,
                exclude_companies=digest_company_excl,
                target_count=5,
            )

            # 3. 논문 수집 (공개 API)
            raw_papers = await self._collect_papers(page_size=20)
            paper_items = self._transform_papers(raw_papers)

            # 4. 통계 수집 (인증 필요 — 실패 시 기본값 사용)
            raw_stats = {}
            try:
                await self._login()
                raw_stats = await self._collect_news_stats()
            except Exception as e:
                logger.warning(f"뉴스 통계 수집 실패 (기본값 사용): {e}")

            # 5. 약물 업데이트 (P2 — 현재는 빈 구조 폴백)
            drug_updates = await self._collect_drug_updates(days=7)

            # 6. 주간 메트릭은 월요일 daily에만 포함 (spec §4.6 옵션 A)
            weekly_metrics: Dict[str, Any] = {}
            if date.today().weekday() == 0:
                weekly_metrics = await self._collect_weekly_metrics()

            now = datetime.now(timezone.utc).isoformat()

            report = {
                "report_date": now,
                "generated_at": now,
                "top_headlines": top_headlines,
                "company_digest": company_digest,
                "papers": paper_items[:20],
                "drug_updates": drug_updates,
                "weekly_metrics": weekly_metrics,
                "stats": {
                    "news_count": raw_stats.get(
                        "total_news", len(top_headlines)
                    ),
                    "paper_count": len(paper_items),
                    "company_count": len(company_digest),
                    "drug_count": drug_updates.get("total", 0),
                    "total_count": (
                        raw_stats.get("total_news", len(top_headlines))
                        + len(paper_items)
                        + len(company_digest)
                    ),
                    "trend_company_count": len(company_digest),
                },
            }

            logger.info(
                f"AllergyInsight 일일 리포트 수집 완료: "
                f"헤드라인 {len(top_headlines)}건, "
                f"기업다이제스트 {len(company_digest)}건, "
                f"논문 {len(paper_items)}건, "
                f"약물 {drug_updates.get('total', 0)}건"
            )
            return report

        except Exception as e:
            logger.error(f"AllergyInsight 일일 리포트 수집 실패: {e}")
            return {}

    async def collect_all(
        self,
        exclude_ids: Optional[list[int]] = None,
        exclude_companies: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        """전체 데이터 수집.

        Args:
            exclude_ids: 최근 발송 기사 ID — daily 리포트 수집에 전달.
            exclude_companies: 최근 발송 기업명 — company_digest dedup 에 전달.
        """
        result = {}

        daily_report = await self.collect_daily_report(
            exclude_ids=exclude_ids,
            exclude_companies=exclude_companies,
        )
        if daily_report:
            result["daily_report"] = daily_report

        logger.info(f"AllergyInsight 전체 수집 완료: {list(result.keys())}")
        return result
