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
