"""
AllergyInsight 데이터 포맷터
API 응답을 이메일 템플릿 컨텍스트로 변환
"""

import logging
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger(__name__)


class AllergyInsightFormatter:
    """AllergyInsight API 응답 → 템플릿 컨텍스트 변환"""

    def format(self, collected_data: Dict[str, Any]) -> Dict[str, Any]:
        """수집 데이터를 템플릿 변수로 변환"""
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

        return {
            "report_date": report_date,
            "top_news": daily_report.get("top_news", []),
            "company_news": daily_report.get("company_news", []),
            "news_groups": daily_report.get("news_groups", []),
            "papers": daily_report.get("papers", []),
            "stats": daily_report.get("stats", {
                "news_count": 0,
                "paper_count": 0,
                "company_count": 0,
                "total_count": 0,
                "trend_company_count": 0,
            }),
            "generated_at": generated_at,
        }

    def format_weekly(self, history_data: list, collected_data: dict = None) -> dict:
        """주간 통계 포매팅 - format_monthly와 동일한 통계 구조 반환

        Args:
            history_data: [{collected_date, data_type, data}, ...]
            collected_data: 추가 수집 데이터 (optional)
        """
        return self._format_stats_report(history_data)

    def format_monthly(self, history_data: list, collected_data: dict = None) -> dict:
        """월간 통계 포매팅 - format_weekly와 동일한 통계 구조 반환

        Args:
            history_data: [{collected_date, data_type, data}, ...]
            collected_data: 추가 수집 데이터 (optional)
        """
        return self._format_stats_report(history_data)

    def _format_stats_report(self, history_data: list) -> dict:
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

        # 원시 데이터 수집
        all_top_news = []
        all_news_groups = []
        all_papers = []
        all_company_news = []
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

            for news in daily_report.get("top_news", []):
                all_top_news.append(news)

            for group in daily_report.get("news_groups", []):
                all_news_groups.append(group)

            for company in daily_report.get("company_news", []):
                all_company_news.append(company)

            for paper in daily_report.get("papers", []):
                all_papers.append(paper)

        # 뉴스 중복 제거 (title 기준)
        seen_titles = set()
        unique_news = []
        for news in all_top_news:
            title = news.get("title", "")
            if title and title not in seen_titles:
                seen_titles.add(title)
                unique_news.append(news)

        # news_groups 내 개별 뉴스도 추가 (중복 제거)
        for group in all_news_groups:
            for item in group.get("items", []):
                title = item.get("title", "")
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    unique_news.append(item)

        # 논문 중복 제거
        seen_paper_titles = set()
        unique_papers = []
        for paper in all_papers:
            title = paper.get("title", "")
            if title and title not in seen_paper_titles:
                seen_paper_titles.add(title)
                unique_papers.append(paper)

        # 기업 뉴스 이름 기준 합치기
        company_names = set()
        for company in all_company_news:
            name = company.get("name", "")
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
        for news in unique_news:
            kw = news.get("keyword") or news.get("search_keyword")
            if kw:
                keyword_counter[kw] += 1

        total_for_kw = max(sum(keyword_counter.values()), 1)
        top_keywords = []
        for kw, count in keyword_counter.most_common(5):
            top_keywords.append({
                "keyword": kw,
                "count": count,
                "percent": round(count / total_for_kw * 100, 1),
            })

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
            "content_type_distribution": content_type_distribution,
            "importance_analysis": importance_analysis,
            "top_journals": top_journals,
            "top_news": top_news,
        }

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
        """데이터 없을 시 빈 기본값"""
        now = datetime.now()
        return {
            "report_date": now,
            "top_news": [],
            "company_news": [],
            "news_groups": [],
            "papers": [],
            "stats": {
                "news_count": 0,
                "paper_count": 0,
                "company_count": 0,
                "total_count": 0,
                "trend_company_count": 0,
            },
            "generated_at": now,
        }
