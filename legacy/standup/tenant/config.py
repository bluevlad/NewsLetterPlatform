"""StandUp 테넌트 설정 (기본값 — 실제 설정은 BaseTenant 프로퍼티/Settings 에서)."""

from ..base import BrandConfig, BrandFeature

TENANT_ID = "standup"
DISPLAY_NAME = "StandUp 주간 인사이트"
EMAIL_SUBJECT_PREFIX = "[StandUp]"
EMAIL_TEMPLATE = "standup/weekly_report.html"

# StandUp accent color (CLAUDE.md): #ef4444 (Red)
BRAND_CONFIG = BrandConfig(
    primary_color="#ef4444",
    primary_color_dark="#b91c1c",
    accent_color="#fca5a5",
    logo_text="StandUp Insight",
    tagline="LogAnalyzer · GitHub QA · Auto-Tobe 통합 주간 인사이트",
    description="3개 소스를 합성한 주간 인사이트를 매주 이메일로 받아보세요",
    features=[
        BrandFeature(
            icon="&#x1F4CA;",
            title="3-소스 통합 KPI",
            description="LogAnalyzer 에러 / GitHub QA 이슈 / Auto-Tobe 진행상황을 한 화면에",
        ),
        BrandFeature(
            icon="&#x26A0;&#xFE0F;",
            title="심각도별 클러스터",
            description="critical / high / medium / info 로 자동 분류된 주요 이벤트",
        ),
        BrandFeature(
            icon="&#x1F3F7;",
            title="서비스 태그",
            description="EduFit · HopenVision · AllergyInsight 등 서비스별 발생 추이",
        ),
        BrandFeature(
            icon="&#x1F4CB;",
            title="주간 헤드라인",
            description="exaone3.5 cascade 가 합성한 7일치 핵심 한 줄 요약",
        ),
    ],
)


# ─────────────────────────────────────────────────────────────
# 테넌트 환경 설정 (P1b, 2026-08-15) — 전역 config.py 에서 이관.
# env 변수명은 필드명 대문자화로 매칭 (기존 변수명 그대로 유지).
# ─────────────────────────────────────────────────────────────

from pathlib import Path

from pydantic_settings import BaseSettings


class StandUpSettings(BaseSettings):
    """StandUp 테넌트 환경 설정 (.env 공유 로드)"""

    # Weekly (매주 월요일 — StandUp 합성 결과 기준)
    standup_weekly_day_of_week: str = "mon"
    standup_weekly_collect_hour: int = 8
    standup_weekly_collect_minute: int = 0
    standup_weekly_send_hour: int = 9
    standup_weekly_send_minute: int = 30

    standup_api_url: str = "http://host.docker.internal:9060"

    class Config:
        env_file = Path(__file__).resolve().parents[3] / ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


tenant_settings = StandUpSettings()
