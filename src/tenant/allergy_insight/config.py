"""
AllergyInsight 테넌트 설정
"""

from ..base import BrandConfig, BrandFeature

TENANT_ID = "allergy-insight"
DISPLAY_NAME = "AllergyInsight 알러지 뉴스 브리핑"
EMAIL_SUBJECT_PREFIX = "[AllergyInsight]"
EMAIL_TEMPLATE = "allergy_insight/daily_report.html"

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
    ],
)
