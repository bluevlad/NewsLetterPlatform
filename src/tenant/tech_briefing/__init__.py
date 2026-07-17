"""TechBriefing 테넌트 — AI 학습·커리어 일일 브리핑.

데이터 소스: SkillRadar 백엔드(9070) 뉴스레터 공급 API
  - GET /api/v1/newsletter/daily — 그날의 큐레이션 4종(course/seminar/policy/news)
  - 수집·정규화·LLM 요약은 SkillRadar 담당, 여기서는 스코어링·편성·발송만

3 sections:
  1. 오늘의 헤드라인 (top 5, 카테고리 다양성)
  2. 카테고리별 다이제스트 (교육과정 / 세미나·행사 / 정책·지원 / 뉴스)
  3. 푸터 미니 리스트 (모집·마감 임박 / 키워드 트렌드)
"""

from typing import Any, Dict, List, Optional

from ..base import BaseTenant, BrandConfig, NEWSLETTER_TYPE_LABELS
from .collector import TechBriefingCollector
from .config import (
    BRAND_CONFIG,
    DISPLAY_NAME,
    EMAIL_SUBJECT_PREFIX,
    EMAIL_TEMPLATE,
    TENANT_ID,
)
from .formatter import TechBriefingFormatter
from ...config import settings


class TechBriefingTenant(BaseTenant):
    """TechBriefing 테넌트 — daily 전용."""

    def __init__(self):
        self._collector = TechBriefingCollector()
        self._formatter = TechBriefingFormatter()

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
        return ["daily"]

    @property
    def schedule_config(self) -> Dict[str, int]:
        return {
            "collect_hour": settings.tech_collect_hour,
            "collect_minute": settings.tech_collect_minute,
            "send_hour": settings.tech_send_hour,
            "send_minute": settings.tech_send_minute,
        }

    async def collect_data(
        self, *,
        exclude_ids: Optional[List[int]] = None,
        exclude_companies: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return await self._collector.collect_daily(
            exclude_ids=exclude_ids,
            exclude_companies=exclude_companies,
        )

    def extract_collection_metrics(self) -> List[Dict[str, Any]]:
        return self._collector.drain_metrics()

    def format_report(self, collected_data: Dict[str, Any]) -> Dict[str, Any]:
        return self._formatter.format(collected_data)

    def generate_subject(self, report_date=None, newsletter_type: str = "daily") -> str:
        from datetime import datetime
        if report_date is None:
            report_date = datetime.now()
        date_str = report_date.strftime("%Y-%m-%d")
        return f"{self.email_subject_prefix} {date_str} AI 학습·커리어 일일 브리핑"
