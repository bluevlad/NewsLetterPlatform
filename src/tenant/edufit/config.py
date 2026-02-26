"""
EduFit 테넌트 설정
"""

from ..base import BrandConfig, BrandFeature

TENANT_ID = "edufit"
DISPLAY_NAME = "EduFit 강사·학원 분석 브리핑"
EMAIL_SUBJECT_PREFIX = "[EduFit]"
EMAIL_TEMPLATE = "edufit/daily_report.html"

BRAND_CONFIG = BrandConfig(
    primary_color="#10b981",
    primary_color_dark="#059669",
    accent_color="#34d399",
    logo_text="EduFit",
    tagline="강사·학원 평판 분석 뉴스레터",
    description="강사 평판 분석과 학원 동향 브리핑을 매일 아침 이메일로 받아보세요",
    features=[
        BrandFeature(
            icon="&#x1F4CA;",
            title="강사 평판 분석",
            description="주요 강사별 온라인 평판 변화를 데이터로 추적합니다",
        ),
        BrandFeature(
            icon="&#x1F3EB;",
            title="학원 동향 분석",
            description="학원별 멘션 수, 감성 점수를 종합하여 랭킹을 제공합니다",
        ),
        BrandFeature(
            icon="&#x2B50;",
            title="추천 분석",
            description="강사 추천 수 변화와 긍정 비율을 분석합니다",
        ),
        BrandFeature(
            icon="&#x1F514;",
            title="이슈 알림",
            description="급격한 평판 변화나 주요 이슈를 빠르게 전달합니다",
        ),
    ],
)
