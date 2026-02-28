"""
EduFit 테넌트 구현
"""

from typing import Any, Dict, List

from ..base import BaseTenant, BrandConfig
from .config import TENANT_ID, DISPLAY_NAME, EMAIL_SUBJECT_PREFIX, EMAIL_TEMPLATE, BRAND_CONFIG
from .collector import EduFitCollector
from .formatter import EduFitFormatter
from ...config import settings


class EduFitTenant(BaseTenant):
    """EduFit 테넌트"""

    def __init__(self):
        self._collector = EduFitCollector()
        self._formatter = EduFitFormatter()

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
        return ["daily", "weekly", "monthly"]

    @property
    def schedule_config(self) -> Dict[str, int]:
        return {
            "collect_hour": settings.edufit_collect_hour,
            "collect_minute": settings.edufit_collect_minute,
            "send_hour": settings.edufit_send_hour,
            "send_minute": settings.edufit_send_minute,
        }

    @property
    def weekly_schedule_config(self) -> Dict[str, Any]:
        return {
            "day_of_week": settings.edufit_weekly_day_of_week,
            "collect_hour": settings.edufit_weekly_collect_hour,
            "collect_minute": settings.edufit_weekly_collect_minute,
            "send_hour": settings.edufit_weekly_send_hour,
            "send_minute": settings.edufit_weekly_send_minute,
        }

    @property
    def monthly_schedule_config(self) -> Dict[str, Any]:
        return {
            "day_of_month": settings.edufit_monthly_day_of_month,
            "collect_hour": settings.edufit_monthly_collect_hour,
            "collect_minute": settings.edufit_monthly_collect_minute,
            "send_hour": settings.edufit_monthly_send_hour,
            "send_minute": settings.edufit_monthly_send_minute,
        }

    async def collect_data(self) -> Dict[str, Any]:
        return await self._collector.collect_all()

    async def collect_summary_data(self, newsletter_type: str,
                                    date_from=None, date_to=None) -> Dict[str, Any]:
        """주간/월간 요약 데이터 수집 (EduFit API의 weekly 엔드포인트 활용)"""
        if newsletter_type == "weekly":
            return await self._collector.collect_weekly_data()
        elif newsletter_type == "monthly":
            return await self._collector.collect_monthly_data()
        return {}

    def format_report(self, collected_data: Dict[str, Any]) -> Dict[str, Any]:
        return self._formatter.format(collected_data)

    def format_summary_report(self, newsletter_type: str,
                               history_data: list,
                               collected_data: Dict[str, Any] = None) -> Dict[str, Any]:
        if newsletter_type == "weekly":
            return self._formatter.format_weekly(history_data, collected_data)
        elif newsletter_type == "monthly":
            return self._formatter.format_monthly(history_data, collected_data)
        return {}
