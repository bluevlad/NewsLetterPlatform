"""
AcademyInsight 테넌트 구현
"""

from typing import Any, Dict

from ..base import BaseTenant, BrandConfig
from .config import TENANT_ID, DISPLAY_NAME, EMAIL_SUBJECT_PREFIX, EMAIL_TEMPLATE, BRAND_CONFIG
from .collector import AcademyInsightCollector
from .formatter import AcademyInsightFormatter
from ...config import settings


class AcademyInsightTenant(BaseTenant):
    """AcademyInsight 테넌트"""

    def __init__(self):
        self._collector = AcademyInsightCollector()
        self._formatter = AcademyInsightFormatter()

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
    def schedule_config(self) -> Dict[str, int]:
        return {
            "collect_hour": settings.academy_collect_hour,
            "collect_minute": settings.academy_collect_minute,
            "send_hour": settings.academy_send_hour,
            "send_minute": settings.academy_send_minute,
        }

    async def collect_data(self) -> Dict[str, Any]:
        return await self._collector.collect_all()

    def format_report(self, collected_data: Dict[str, Any]) -> Dict[str, Any]:
        return self._formatter.format(collected_data)
