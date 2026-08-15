"""
AllergyInsight 테넌트 설정
"""

from ..base import BrandConfig, BrandFeature

TENANT_ID = "allergy-insight"
DISPLAY_NAME = "AllergyInsight 알러지 뉴스 브리핑"
EMAIL_SUBJECT_PREFIX = "[AllergyInsight]"
EMAIL_TEMPLATE = "allergy_insight/daily_report.html"

# 재구성 섹션 전용 컬러 토큰 (NEWSLETTER_REDESIGN_SPEC §4.5).
# BrandConfig가 공용 스키마라 필드 추가 대신 테넌트 전용 상수로 둔다.
DRUG_SECTION_COLOR = "#00838f"
DRUG_SECTION_BG = "#e0f7fa"

BRAND_CONFIG = BrandConfig(
    primary_color="#2e7d32",
    primary_color_dark="#1b5e20",
    accent_color="#66bb6a",
    logo_text="AllergyInsight",
    tagline="알러지 뉴스 & 논문 브리핑",
    description="알러지 관련 최신 뉴스, 논문, 기업 동향을 매일 아침 이메일로 받아보세요",
    features=[
        BrandFeature(
            icon="&#x1F4F0;",
            title="알러지 뉴스",
            description="네이버 뉴스에서 알러지 관련 주요 소식을 수집하여 전달합니다",
        ),
        BrandFeature(
            icon="&#x1F4DA;",
            title="최신 논문",
            description="PubMed에서 알러지 분야 최신 논문을 매일 브리핑합니다",
        ),
        BrandFeature(
            icon="&#x1F3E2;",
            title="기업 동향",
            description="알러지 관련 기업들의 최신 뉴스와 동향을 분석합니다",
        ),
        BrandFeature(
            icon="&#x2B50;",
            title="중요도 분석",
            description="AI가 뉴스의 중요도를 분석하여 핵심 소식을 선별합니다",
        ),
        BrandFeature(
            icon="&#x1F48A;",
            title="약물 업데이트",
            description="openFDA·MFDS 기반 알러지 치료제 승인·라벨 변경·경고를 요약합니다",
        ),
        BrandFeature(
            icon="&#x1F52C;",
            title="알러지 인사이트 스폿라이트",
            description="가장 오래 다루지 않은 알러젠을 골라 새로 확인된 논문과 처방 참고 정보를 전해드립니다",
        ),
        BrandFeature(
            icon="&#x1F489;",
            title="신흥 치료법",
            description="신규/상승 추세의 알러지 치료법(면역요법·생물학제 등)을 큐레이션합니다",
        ),
        BrandFeature(
            icon="&#x1F321;",
            title="알러젠 트렌드",
            description="논문 언급 기준 상승·하락 알러젠 Top 5 와 주요 연관 영역을 제공합니다",
        ),
    ],
)


# ─────────────────────────────────────────────────────────────
# 테넌트 환경 설정 (P1b, 2026-08-15)
# 전역 src/config.py 에 흩어져 있던 AllergyInsight 필드를 이관 —
# 테넌트 추가/제거 시 플랫폼 전역 설정을 건드리지 않기 위함.
# env 변수명은 필드명 대문자화로 매칭 (기존 변수명 그대로 유지).
# ─────────────────────────────────────────────────────────────

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings


class AllergyInsightSettings(BaseSettings):
    """AllergyInsight 테넌트 환경 설정 (.env 공유 로드)"""

    # Daily 스케줄 — 발송은 슬롯(early/mid/late)이 결정, SEND_HOUR/MINUTE 는
    # deprecated 이지만 schedule_config 계약 호환을 위해 유지.
    allergy_collect_hour: int = 5
    allergy_collect_minute: int = 0
    allergy_send_hour: int = 8
    allergy_send_minute: int = 30

    # Weekly (매주 금요일)
    allergy_weekly_day_of_week: str = "fri"
    allergy_weekly_collect_hour: int = 5
    allergy_weekly_collect_minute: int = 0
    allergy_weekly_send_hour: int = 9
    allergy_weekly_send_minute: int = 30

    # Monthly (매월 말일)
    allergy_monthly_day_of_month: str = "last"
    allergy_monthly_collect_hour: int = 5
    allergy_monthly_collect_minute: int = 0
    allergy_monthly_send_hour: int = 10
    allergy_monthly_send_minute: int = 0

    # Backend API
    allergy_insight_api_url: str = "http://localhost:9040"
    # 페르소나 적응형 뉴스레터 인증 키 — AllergyInsight 측 NEWSLETTER_API_KEY 와
    # 동일 값. 빈 값이면 페르소나 기능 graceful degrade.
    # 필드명(_api_key)과 env 명(_KEY)이 달라 validation_alias 필수.
    allergy_insight_newsletter_api_key: str = Field(
        default="", validation_alias="ALLERGY_INSIGHT_NEWSLETTER_KEY"
    )
    # v2 API 관리자 인증 (ADR-002)
    allergy_insight_admin_name: str = ""
    allergy_insight_admin_phone: str = ""
    allergy_insight_admin_pin: str = ""

    class Config:
        env_file = Path(__file__).resolve().parents[3] / ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


tenant_settings = AllergyInsightSettings()
