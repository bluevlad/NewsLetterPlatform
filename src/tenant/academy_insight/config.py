"""
AcademyInsight 테넌트 설정
"""

from ..base import BrandConfig, BrandFeature

TENANT_ID = "academy-insight"
DISPLAY_NAME = "AcademyInsight 학원 동향 브리핑"
EMAIL_SUBJECT_PREFIX = "[AcademyInsight]"
EMAIL_TEMPLATE = "academy_insight/daily_report.html"

BRAND_CONFIG = BrandConfig(
    primary_color="#8b5cf6",
    primary_color_dark="#7c3aed",
    accent_color="#a78bfa",
    logo_text="AcademyInsight",
    tagline="학원 동향 분석 뉴스레터",
    description="학원 온라인 평판 분석 일일 브리핑을 매일 아침 이메일로 받아보세요",
    features=[
        BrandFeature(
            icon="&#x1F3EB;",
            title="학원 동향 분석",
            description="지역별 학원 평판과 수강 동향을 데이터로 분석합니다",
        ),
        BrandFeature(
            icon="&#x2B50;",
            title="리뷰 인사이트",
            description="학부모 리뷰와 평점 변화를 종합적으로 추적합니다",
        ),
        BrandFeature(
            icon="&#x1F4CD;",
            title="지역 비교",
            description="지역별 학원 경쟁 현황과 트렌드를 비교 분석합니다",
        ),
    ],
)
