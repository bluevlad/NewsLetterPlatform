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
        """주간 요약 포매팅 - 7일간 일일 이력 데이터를 집계

        Args:
            history_data: [{collected_date, data_type, data}, ...]
            collected_data: 추가 수집 데이터 (optional)
        """
        from collections import defaultdict
        from datetime import date, timedelta

        # 일별로 그룹핑
        by_date = defaultdict(dict)
        for record in history_data:
            d = record["collected_date"]
            dtype = record["data_type"]
            data = record["data"]
            by_date[d][dtype] = data

        # 뉴스/논문/기업 집계
        all_top_news = []
        all_company_news = []
        all_papers = []
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

            # 주요 뉴스 수집 (중복 제거: title 기준)
            for news in daily_report.get("top_news", []):
                all_top_news.append(news)

            for company in daily_report.get("company_news", []):
                all_company_news.append(company)

            for paper in daily_report.get("papers", []):
                all_papers.append(paper)

        # 뉴스 중복 제거 (title 기준)
        seen_titles = set()
        unique_top_news = []
        for news in all_top_news:
            title = news.get("title", "")
            if title and title not in seen_titles:
                seen_titles.add(title)
                unique_top_news.append(news)

        # 중요도 순 정렬
        unique_top_news.sort(
            key=lambda x: x.get("importance_score", 0) or 0, reverse=True
        )

        # 논문 중복 제거
        seen_paper_titles = set()
        unique_papers = []
        for paper in all_papers:
            title = paper.get("title", "")
            if title and title not in seen_paper_titles:
                seen_paper_titles.add(title)
                unique_papers.append(paper)

        # 기업 뉴스 이름 기준 합치기
        company_map = {}
        for company in all_company_news:
            name = company.get("name", "")
            if name not in company_map:
                company_map[name] = company.copy()
            else:
                existing = company_map[name]
                existing_articles = existing.get("articles", [])
                new_articles = company.get("articles", [])
                # 제목 기준 중복 제거
                existing_titles = {a.get("title", "") for a in existing_articles}
                for article in new_articles:
                    if article.get("title", "") not in existing_titles:
                        existing_articles.append(article)
                existing["articles"] = existing_articles

        # 기간 계산
        today = date.today()
        period_end = today - timedelta(days=today.weekday())
        period_start = period_end - timedelta(days=7)
        period_end = period_end - timedelta(days=1)

        return {
            "report_date": datetime.now(),
            "period_start": period_start,
            "period_end": period_end,
            "top_news": unique_top_news[:10],
            "company_news": list(company_map.values()),
            "papers": unique_papers[:10],
            "stats": {
                "news_count": total_news_count,
                "paper_count": total_paper_count,
                "company_count": total_company_count,
                "total_count": total_news_count + total_paper_count + total_company_count,
                "days_with_data": days_with_data,
            },
            "generated_at": datetime.now(),
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
