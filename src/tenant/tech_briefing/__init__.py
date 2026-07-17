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
        return ["daily", "weekly"]

    @property
    def schedule_config(self) -> Dict[str, int]:
        return {
            "collect_hour": settings.tech_collect_hour,
            "collect_minute": settings.tech_collect_minute,
            "send_hour": settings.tech_send_hour,
            "send_minute": settings.tech_send_minute,
        }

    @property
    def weekly_schedule_config(self) -> Dict[str, Any]:
        return {
            "day_of_week": settings.tech_weekly_day_of_week,
            "collect_hour": settings.tech_weekly_collect_hour,
            "collect_minute": settings.tech_weekly_collect_minute,
            "send_hour": settings.tech_weekly_send_hour,
            "send_minute": settings.tech_weekly_send_minute,
        }

    @property
    def dedup_recent_days(self) -> Optional[int]:
        """최근 7일 발송 항목 재노출 차단 — SkillRadar 정정 upsert 가
        fetched_at 을 갱신해 같은 리소스가 재수집되는 케이스 방어."""
        return 7

    async def collect_data(
        self, *,
        exclude_ids: Optional[List[int]] = None,
        exclude_companies: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return await self._collector.collect_daily(
            exclude_ids=exclude_ids,
            exclude_companies=exclude_companies,
        )

    async def collect_summary_data(self, newsletter_type: str,
                                    date_from=None, date_to=None) -> Dict[str, Any]:
        """weekly: SkillRadar 공개 통계 (보조 데이터 — 본문은 daily 이력 집계)."""
        if newsletter_type != "weekly":
            return {}
        return await self._collector.collect_weekly_summary()

    def extract_collection_metrics(self) -> List[Dict[str, Any]]:
        return self._collector.drain_metrics()

    def format_report(self, collected_data: Dict[str, Any]) -> Dict[str, Any]:
        return self._formatter.format(collected_data)

    def format_summary_report(self, newsletter_type: str,
                               history_data: list,
                               collected_data: Dict[str, Any] = None) -> Dict[str, Any]:
        if newsletter_type != "weekly":
            return {}
        return self._formatter.format_weekly(history_data, collected_data)

    def extract_sent_article_entries(
        self, context: Dict[str, Any]
    ) -> List[tuple]:
        """daily 발송 context 에서 sent_articles 기록 대상 추출.

        메일에 노출된 항목 전부(headlines + digest entries)를 기록 —
        다음 7일간 동일 리소스 재노출 차단. article_id 는 collector 가
        SkillRadar UUID 에서 파생한 63-bit dedup_id.
        """
        entries: List[tuple] = []
        for h in context.get("headlines") or []:
            if h.get("dedup_id") is not None:
                entries.append((int(h["dedup_id"]), h.get("url"), "headline", None))
        for group in context.get("digest_groups") or []:
            for it in group.get("entries") or []:
                if it.get("dedup_id") is not None:
                    entries.append((int(it["dedup_id"]), it.get("url"), "digest", None))
        return entries

    def generate_subject(self, report_date=None, newsletter_type: str = "daily") -> str:
        from datetime import datetime
        if report_date is None:
            report_date = datetime.now()
        date_str = report_date.strftime("%Y-%m-%d")
        label = "주간 브리핑" if newsletter_type == "weekly" else "일일 브리핑"
        return f"{self.email_subject_prefix} {date_str} AI 학습·커리어 {label}"
