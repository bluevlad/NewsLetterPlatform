"""
StandUp 테넌트 — 주간 Insight 뉴스레터.

원본 서비스: StandUp (port 9060) `/api/v1/insight/*`.
LogAnalyzer / GitHub QA Issues / Auto-Tobe 3개 소스를 풀 방식으로 수집해
exaone3.5 cascade + pgvector RAG 로 합성한 주간 뉴스레터.
NewsLetterPlatform 측에서는 events 와 latest-newsletter KPI 를 가져와
자체 Jinja 템플릿으로 재포맷한다.
"""

from typing import Any, Dict, List, Optional

from ..base import BaseTenant, BrandConfig, NEWSLETTER_TYPE_LABELS
from .collector import StandUpCollector
from .config import (
    BRAND_CONFIG,
    DISPLAY_NAME,
    EMAIL_SUBJECT_PREFIX,
    EMAIL_TEMPLATE,
    TENANT_ID,
)
from .formatter import StandUpFormatter
from ...config import settings


class StandUpTenant(BaseTenant):
    """StandUp 테넌트 — weekly 전용."""

    def __init__(self):
        self._collector = StandUpCollector()
        self._formatter = StandUpFormatter()

    @property
    def tenant_id(self) -> str:
        return TENANT_ID

    @property
    def display_name(self) -> str:
        return DISPLAY_NAME

    @property
    def email_subject_prefix(self) -> str:
        return EMAIL_SUBJECT_PREFIX

    @property
    def email_template(self) -> str:
        return EMAIL_TEMPLATE

    @property
    def brand_config(self) -> BrandConfig:
        return BRAND_CONFIG

    @property
    def supported_frequencies(self) -> List[str]:
        return ["weekly"]

    @property
    def schedule_config(self) -> Dict[str, int]:
        # daily 미지원이지만 base interface 호환을 위해 placeholder 반환.
        return {
            "collect_hour": 0,
            "collect_minute": 0,
            "send_hour": 0,
            "send_minute": 0,
        }

    @property
    def weekly_schedule_config(self) -> Dict[str, Any]:
        return {
            "day_of_week": settings.standup_weekly_day_of_week,
            "collect_hour": settings.standup_weekly_collect_hour,
            "collect_minute": settings.standup_weekly_collect_minute,
            "send_hour": settings.standup_weekly_send_hour,
            "send_minute": settings.standup_weekly_send_minute,
        }

    async def collect_data(
        self, *,
        exclude_ids: Optional[List[int]] = None,
        exclude_companies: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        # daily 미지원 — 빈 dict 반환.
        return {}

    def format_report(self, collected_data: Dict[str, Any]) -> Dict[str, Any]:
        return {}

    async def collect_summary_data(self, newsletter_type: str,
                                   date_from=None, date_to=None) -> Dict[str, Any]:
        if newsletter_type != "weekly":
            return {}
        return await self._collector.collect_weekly(date_from, date_to)

    def extract_collection_metrics(self) -> List[Dict[str, Any]]:
        return self._collector.drain_metrics()

    def format_summary_report(self, newsletter_type: str,
                              history_data: list,
                              collected_data: Dict[str, Any] = None) -> Dict[str, Any]:
        if newsletter_type != "weekly":
            return {}
        return self._formatter.format_weekly(collected_data or {})

    def generate_subject(self, report_date=None, newsletter_type: str = "weekly") -> str:
        from datetime import datetime
        if report_date is None:
            report_date = datetime.now()
        date_str = report_date.strftime("%Y-%m-%d")
        if newsletter_type == "weekly":
            return f"{self.email_subject_prefix} {date_str} 주간 인사이트"
        label = NEWSLETTER_TYPE_LABELS.get(newsletter_type, "브리핑")
        return f"{self.email_subject_prefix} {date_str} {label}"
