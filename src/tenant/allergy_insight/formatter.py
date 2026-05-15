"""
AllergyInsight 데이터 포맷터
API 응답을 이메일 템플릿 컨텍스트로 변환
"""

import logging
from datetime import datetime
from typing import Any, Dict

from .config import DRUG_SECTION_BG, DRUG_SECTION_COLOR

logger = logging.getLogger(__name__)


def _empty_drug_updates() -> Dict[str, Any]:
    """drug_updates 기본값 (total=0 이면 템플릿이 섹션 숨김)."""
    return {
        "new_approvals": [],
        "label_changes": [],
        "blackbox_warnings": [],
        "recalls": [],
        "total": 0,
    }


class AllergyInsightFormatter:
    """AllergyInsight API 응답 → 템플릿 컨텍스트 변환"""

    def format(self, collected_data: Dict[str, Any]) -> Dict[str, Any]:
        """수집 데이터를 템플릿 변수로 변환.

        Phase 1 전환 완료: top_headlines/company_digest/drug_updates/weekly_metrics
        키만 사용. 기존 Phase 0 키(top_news/company_news/news_groups) 제거.
        Spec: NEWSLETTER_REDESIGN_SPEC §4.3
        """
        daily_report = collected_data.get("daily_report", {})

        if not daily_report:
            return self._empty_context()

        # ISO 문자열 → datetime 변환
        report_date = self._parse_datetime(
            daily_report.get("report_date"),
            default=datetime.now(),
        )
        generated_at = self._parse_datetime(
            daily_report.get("generated_at"),
            default=datetime.now(),
        )

        drug_updates = daily_report.get("drug_updates") or _empty_drug_updates()
        # 방어: 백엔드가 배열만 주고 total 누락한 경우 재계산
        if "total" not in drug_updates:
            drug_updates["total"] = (
                len(drug_updates.get("new_approvals", []))
                + len(drug_updates.get("label_changes", []))
                + len(drug_updates.get("blackbox_warnings", []))
                + len(drug_updates.get("recalls", []))
            )

        return {
            "report_date": report_date,
            "top_headlines": daily_report.get("top_headlines", []),
            "company_digest": daily_report.get("company_digest", []),
            "papers": daily_report.get("papers", []),
            "drug_updates": drug_updates,
            "weekly_metrics": daily_report.get("weekly_metrics") or {},
            # N2 신규 (collector 가 산출, formatter 는 패스스루)
            "spotlight": daily_report.get("spotlight"),
            "treatments": daily_report.get("treatments") or {},
            "trends_rising": daily_report.get("trends_rising", []),
            "trends_declining": daily_report.get("trends_declining", []),
            "drug_section_color": DRUG_SECTION_COLOR,
            "drug_section_bg": DRUG_SECTION_BG,
            "stats": daily_report.get("stats", {
                "news_count": 0,
                "paper_count": 0,
                "company_count": 0,
                "drug_count": 0,
                "total_count": 0,
                "trend_company_count": 0,
            }),
            "generated_at": generated_at,
        }

    def format_weekly(self, history_data: list, collected_data: dict = None) -> dict:
        """주간 통계 포매팅 - format_monthly와 동일한 통계 구조 반환

        Args:
            history_data: [{collected_date, data_type, data}, ...]
            collected_data: 추가 수집 데이터 (optional). weekly_metrics 키가 있으면 전달.
                `_prev_history` 키가 있으면 직전 주 history_data 로 간주하고 Δ + 자동 코멘트 계산.
        """
        result = self._format_stats_report(history_data, collected_data)

        # Phase 3: Week-over-Week Δ + 자동 코멘트
        # _prev_history 가 부족하면(<2일) Δ 계산을 생략 — 오해 유발 방지
        prev_history = (collected_data or {}).get("_prev_history") or []
        prev_days = len({r["collected_date"] for r in prev_history if "collected_date" in r})
        if prev_history and prev_days >= 2:
            prev_result = self._format_stats_report(prev_history)
            result["deltas"] = self._compute_deltas(result, prev_result)
            result["auto_comments"] = self._generate_comments(
                result, prev_result, result["deltas"]
            )
        else:
            result["deltas"] = None
            result["auto_comments"] = []
            if prev_history:
                logger.info(
                    "[allergy_insight][weekly] prev_history 데이터 부족 "
                    "(prev_days=%d < 2) → Δ 계산 생략",
                    prev_days,
                )
        return result

    def format_monthly(self, history_data: list, collected_data: dict = None) -> dict:
        """월간 통계 포매팅 - format_weekly와 동일한 통계 구조 반환

        Args:
            history_data: [{collected_date, data_type, data}, ...]
            collected_data: 추가 수집 데이터 (optional). weekly_metrics 키가 있으면 전달.
        """
        return self._format_stats_report(history_data, collected_data)

    def _format_stats_report(
        self, history_data: list, collected_data: dict = None
    ) -> dict:
        """주간/월간 공통 통계 리포트 포매팅"""
        from collections import Counter, defaultdict
        from datetime import date, timedelta

        # 일별로 그룹핑
        by_date = defaultdict(dict)
        for record in history_data:
            d = record["collected_date"]
            dtype = record["data_type"]
            data = record["data"]
            by_date[d][dtype] = data

        # 원시 데이터 수집 (Phase 1: top_headlines + company_digest 경로)
        all_headlines = []
        all_papers = []
        all_company_digest = []
        total_news_count = 0
        total_paper_count = 0
        total_company_count = 0
        days_with_data = 0

        for collected_date in sorted(by_date.keys()):
            day_data = by_date[collected_date]
            daily_report = day_data.get("daily_report", {})
            if not daily_report:
                continue

            days_with_data += 1
            day_stats = daily_report.get("stats", {})
            total_news_count += day_stats.get("news_count", 0)
            total_paper_count += day_stats.get("paper_count", 0)
            total_company_count += day_stats.get("company_count", 0)

            # Phase 1 키 우선, Phase 0 키 폴백 (과거 히스토리 데이터 호환)
            headlines = daily_report.get("top_headlines", [])
            if headlines:
                all_headlines.extend(headlines)
            else:
                for news in daily_report.get("top_news", []):
                    all_headlines.append(news)

            digest = daily_report.get("company_digest", [])
            if digest:
                all_company_digest.extend(digest)
            else:
                for company in daily_report.get("company_news", []):
                    all_company_digest.append(company)

            for paper in daily_report.get("papers", []):
                all_papers.append(paper)

        # 뉴스 중복 제거 (title 기준)
        seen_titles = set()
        unique_news = []
        for news in all_headlines:
            title = news.get("title", "")
            if title and title not in seen_titles:
                seen_titles.add(title)
                unique_news.append(news)

        # 논문 중복 제거
        seen_paper_titles = set()
        unique_papers = []
        for paper in all_papers:
            title = paper.get("title", "")
            if title and title not in seen_paper_titles:
                seen_paper_titles.add(title)
                unique_papers.append(paper)

        # 기업 이름 수집
        company_names = set()
        for item in all_company_digest:
            name = item.get("company_name") or item.get("name", "")
            if name:
                company_names.add(name)

        # --- 통계 집계 ---

        total_unique_news = len(unique_news)
        total_unique_papers = len(unique_papers)
        total_companies = len(company_names)

        # 1. 카테고리 분포 (뉴스)
        category_counter = Counter()
        for news in unique_news:
            cat = news.get("category") or "기타"
            category_counter[cat] += 1

        category_colors = {
            "임상/치료": "#2e7d32",
            "연구/학술": "#1565c0",
            "생활/관리": "#ef6c00",
            "산업/규제": "#6a1b9a",
            "기타": "#757575",
        }
        total_for_cat = max(sum(category_counter.values()), 1)
        category_distribution = []
        for name, count in category_counter.most_common():
            category_distribution.append({
                "name": name,
                "count": count,
                "percent": round(count / total_for_cat * 100, 1),
                "color": category_colors.get(name, "#757575"),
            })

        # 2. 키워드 분포 (뉴스 keyword 필드)
        keyword_counter = Counter()
        news_with_keyword = 0
        for news in unique_news:
            kw = news.get("keyword") or news.get("search_keyword")
            if kw:
                keyword_counter[kw] += 1
                news_with_keyword += 1

        total_for_kw = max(sum(keyword_counter.values()), 1)
        top_keywords = []
        for kw, count in keyword_counter.most_common(5):
            top_keywords.append({
                "keyword": kw,
                "count": count,
                "percent": round(count / total_for_kw * 100, 1),
            })

        # top_keywords가 비었을 때 사유를 분류해 템플릿에 안내 박스로 노출
        # (조용한 섹션 누락 방지 — 회의/관리자 알림에서 즉시 인지 가능)
        top_keywords_status: Dict[str, Any] | None = None
        if not top_keywords:
            if total_unique_news == 0:
                top_keywords_status = {
                    "reason": "no_news",
                    "message": "이번 주 수집된 뉴스가 없어 키워드 집계가 불가합니다.",
                    "hint": "수집 스케줄러 또는 백엔드 API 응답을 확인해 주세요.",
                }
                logger.warning(
                    "[allergy_insight][weekly] top_keywords 비활성: "
                    "수집된 뉴스 0건 (days_with_data=%d)",
                    days_with_data,
                )
            else:
                top_keywords_status = {
                    "reason": "missing_keyword_field",
                    "message": (
                        f"뉴스 {total_unique_news}건 중 keyword 필드가 모두 비어 있어 "
                        "키워드 집계를 생성하지 못했습니다."
                    ),
                    "hint": (
                        "AllergyInsight Backend 응답의 search_keyword 필드 결측일 가능성이 있습니다."
                    ),
                }
                logger.warning(
                    "[allergy_insight][weekly] top_keywords 비활성: "
                    "unique_news=%d 인데 keyword 필드가 모두 결측",
                    total_unique_news,
                )

        # 3. 뉴스 vs 논문 비율
        total_content = max(total_unique_news + total_unique_papers, 1)
        content_type_distribution = [
            {
                "name": "뉴스",
                "count": total_unique_news,
                "percent": round(total_unique_news / total_content * 100, 1),
                "color": "#2e7d32",
            },
            {
                "name": "논문",
                "count": total_unique_papers,
                "percent": round(total_unique_papers / total_content * 100, 1),
                "color": "#1565c0",
            },
        ]

        # 4. 중요도 분석 (뉴스)
        importance_scores = [
            n.get("importance_score", 0) or 0 for n in unique_news
        ]
        avg_importance = (
            round(sum(importance_scores) / max(len(importance_scores), 1), 2)
        )
        high_count = sum(1 for s in importance_scores if s >= 0.7)
        mid_count = sum(1 for s in importance_scores if 0.4 <= s < 0.7)
        low_count = sum(1 for s in importance_scores if s < 0.4)
        imp_total = max(high_count + mid_count + low_count, 1)

        importance_analysis = {
            "avg_score": avg_importance,
            "high_count": high_count,
            "high_percent": round(high_count / imp_total * 100, 1),
            "mid_count": mid_count,
            "mid_percent": round(mid_count / imp_total * 100, 1),
            "low_count": low_count,
            "low_percent": round(low_count / imp_total * 100, 1),
        }

        # 5. 논문 저널 TOP 5
        journal_counter = Counter()
        for paper in unique_papers:
            journal = paper.get("journal")
            if journal:
                journal_counter[journal] += 1

        total_for_journal = max(sum(journal_counter.values()), 1)
        top_journals = []
        for journal, count in journal_counter.most_common(5):
            top_journals.append({
                "journal": journal,
                "count": count,
                "percent": round(count / total_for_journal * 100, 1),
            })

        # 6. 핵심 뉴스 TOP 3
        unique_news.sort(
            key=lambda x: x.get("importance_score", 0) or 0, reverse=True
        )
        top_news = []
        for rank, news in enumerate(unique_news[:3], 1):
            top_news.append({
                "rank": rank,
                "title": news.get("title", ""),
                "link": news.get("link", ""),
                "summary": news.get("summary") or news.get("description") or "",
                "importance_score": news.get("importance_score", 0) or 0,
                "category": news.get("category", ""),
                "source": news.get("source", ""),
                "pub_date": news.get("pub_date", ""),
            })

        # 기간 계산: 실제 데이터 날짜 기준
        if by_date:
            sorted_dates = sorted(by_date.keys())
            period_start = sorted_dates[0]
            period_end = sorted_dates[-1]
        else:
            today = date.today()
            period_end = today - timedelta(days=1)
            period_start = today - timedelta(days=7)

        # Phase 1: weekly_metrics 패스스루 (collected_data에 있으면)
        weekly_metrics = (collected_data or {}).get("weekly_metrics") or {}

        return {
            "report_date": datetime.now(),
            "period_start": period_start,
            "period_end": period_end,
            "generated_at": datetime.now(),
            "summary": {
                "days_with_data": days_with_data,
                "total_news": total_unique_news,
                "total_papers": total_unique_papers,
                "total_companies": total_companies,
                "avg_importance": avg_importance,
                "daily_avg_news": round(total_news_count / max(days_with_data, 1), 1),
                "daily_avg_papers": round(total_paper_count / max(days_with_data, 1), 1),
            },
            "category_distribution": category_distribution,
            "top_keywords": top_keywords,
            "top_keywords_status": top_keywords_status,
            "content_type_distribution": content_type_distribution,
            "importance_analysis": importance_analysis,
            "top_journals": top_journals,
            "top_news": top_news,
            "weekly_metrics": weekly_metrics,
            "drug_section_color": DRUG_SECTION_COLOR,
            "drug_section_bg": DRUG_SECTION_BG,
        }

    @staticmethod
    def _compute_deltas(curr: dict, prev: dict) -> Dict[str, Any]:
        """이번 주(curr) vs 직전 주(prev) Δ 계산.

        각 지표마다 abs(절대값 차이) + pct(%) + arrow(▲/▼/─) 동시 산출.
        분모 0 일 때 pct 는 None 으로 두고 템플릿에서 분기.
        importance/high/mid/low 는 pp(percentage point) 단위.
        """
        def _pct(curr_v: float, prev_v: float) -> float | None:
            if prev_v in (0, None):
                return None
            return round((curr_v - prev_v) / prev_v * 100, 1)

        def _arrow(diff: float) -> str:
            if diff is None or diff == 0:
                return "─"
            return "▲" if diff > 0 else "▼"

        c_sum = curr.get("summary", {})
        p_sum = prev.get("summary", {})
        c_imp = curr.get("importance_analysis", {})
        p_imp = prev.get("importance_analysis", {})

        # 키워드 신규 진입 (이번 주에만 있고 지난주에는 없는 키워드)
        curr_kws = {k["keyword"] for k in curr.get("top_keywords", [])}
        prev_kws = {k["keyword"] for k in prev.get("top_keywords", [])}
        new_keywords = sorted(curr_kws - prev_kws)
        dropped_keywords = sorted(prev_kws - curr_kws)

        news_abs = c_sum.get("total_news", 0) - p_sum.get("total_news", 0)
        papers_abs = c_sum.get("total_papers", 0) - p_sum.get("total_papers", 0)
        comp_abs = c_sum.get("total_companies", 0) - p_sum.get("total_companies", 0)
        avg_imp_pp = round(
            (c_sum.get("avg_importance", 0) - p_sum.get("avg_importance", 0)) * 100, 1
        )
        high_pp = round(
            c_imp.get("high_percent", 0) - p_imp.get("high_percent", 0), 1
        )

        return {
            "news_abs": news_abs,
            "news_pct": _pct(c_sum.get("total_news", 0), p_sum.get("total_news", 0)),
            "news_arrow": _arrow(news_abs),
            "papers_abs": papers_abs,
            "papers_pct": _pct(c_sum.get("total_papers", 0), p_sum.get("total_papers", 0)),
            "papers_arrow": _arrow(papers_abs),
            "companies_abs": comp_abs,
            "companies_arrow": _arrow(comp_abs),
            "avg_importance_pp": avg_imp_pp,
            "avg_importance_arrow": _arrow(avg_imp_pp),
            "high_pp": high_pp,
            "high_arrow": _arrow(high_pp),
            "prev_period_label": (
                f"{prev.get('period_start')} ~ {prev.get('period_end')}"
            ),
            "new_keywords": new_keywords,
            "dropped_keywords": dropped_keywords,
        }

    @staticmethod
    def _generate_comments(
        curr: dict, prev: dict, deltas: Dict[str, Any]
    ) -> list[Dict[str, str]]:
        """규칙 기반 1줄 한국어 자동 해석 코멘트.

        Returns:
            [{text, severity}] — severity: "info" | "warning"
        """
        comments: list[Dict[str, str]] = []
        c_sum = curr.get("summary", {})
        c_imp = curr.get("importance_analysis", {})
        p_imp = prev.get("importance_analysis", {})

        # 규칙 1: News Δ ≥ +20%
        news_pct = deltas.get("news_pct")
        if news_pct is not None and news_pct >= 20:
            comments.append({
                "text": (
                    f"💬 이번 주 뉴스 유입량이 평소보다 많습니다 "
                    f"(전주 대비 +{news_pct}%)."
                ),
                "severity": "info",
            })

        # 규칙 2: High 비율 Δ ≥ +5pp
        high_pp = deltas.get("high_pp", 0)
        if high_pp >= 5:
            comments.append({
                "text": (
                    f"💬 고중요도 콘텐츠 비중이 늘었습니다 "
                    f"(전주 {p_imp.get('high_percent', 0)}% → "
                    f"이번주 {c_imp.get('high_percent', 0)}%)."
                ),
                "severity": "info",
            })

        # 규칙 3: 신규 키워드 ≥ 2개
        new_kws = deltas.get("new_keywords", [])
        if len(new_kws) >= 2:
            preview = ", ".join(new_kws[:3])
            more = f" 외 {len(new_kws) - 3}개" if len(new_kws) > 3 else ""
            comments.append({
                "text": f"💬 신규 키워드 {len(new_kws)}개 등장: {preview}{more}.",
                "severity": "info",
            })

        # 규칙 4: News Δ ≤ -30% & days_with_data < 6 → 수집 실패 의심
        days_with_data = c_sum.get("days_with_data", 7)
        if news_pct is not None and news_pct <= -30 and days_with_data < 6:
            comments.append({
                "text": (
                    f"⚠️ 수집 실패 가능성 — 뉴스 유입이 전주 대비 {news_pct}% 감소, "
                    f"수집 성공일 {days_with_data}일. 수집 시스템 점검 필요."
                ),
                "severity": "warning",
            })

        return comments

    @staticmethod
    def _parse_datetime(value: str, default: datetime = None) -> datetime:
        """ISO 문자열 → datetime 변환"""
        if not value:
            return default or datetime.now()
        try:
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return default or datetime.now()

    @staticmethod
    def _empty_context() -> Dict[str, Any]:
        """데이터 없을 시 빈 기본값 (Phase 1 + N2 키)."""
        now = datetime.now()
        return {
            "report_date": now,
            "top_headlines": [],
            "company_digest": [],
            "papers": [],
            "drug_updates": _empty_drug_updates(),
            "weekly_metrics": {},
            # N2 신규
            "spotlight": None,
            "treatments": {},
            "trends_rising": [],
            "trends_declining": [],
            "drug_section_color": DRUG_SECTION_COLOR,
            "drug_section_bg": DRUG_SECTION_BG,
            "stats": {
                "news_count": 0,
                "paper_count": 0,
                "company_count": 0,
                "drug_count": 0,
                "total_count": 0,
                "trend_company_count": 0,
            },
            "generated_at": now,
        }
