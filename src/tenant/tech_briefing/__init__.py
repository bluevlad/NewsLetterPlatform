"""TechBriefing 테넌트 — AI/LLM 일일 기술 브리핑.

3 sources MVP:
  - GitHub Releases (PyTorch/Transformers/vLLM/Ollama/LangChain/MLX …)
  - NVD CVE feed (AI 툴체인 + 운영 스택 Spring/Tomcat 잔존)
  - 공식 블로그 RSS (OpenAI · Hugging Face · Google AI · DeepMind · PyTorch · Ollama)

3 sections:
  1. 오늘의 헤드라인 (top 5, 1프로젝트 1)
  2. 릴리즈 & 보안 (new_releases / breaking_changes / cves / deprecations)
  3. 키워드 트렌드 (rising)
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
        return f"{self.email_subject_prefix} {date_str} AI/LLM 일일 브리핑"
