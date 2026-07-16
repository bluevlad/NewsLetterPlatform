"""TechBriefing 테넌트 설정 — AI 학습·커리어 일일 브리핑.

3 sources (SkillRadar 수집 대상과 동일 기반):
  1. 뉴스 키워드 검색 — Google News RSS (AI 교육/부트캠프/강의)
  2. 정책 — 정부 정책 RSS(korea.kr) + 정책 키워드 뉴스 검색
  3. 교육·세미나 — 교육/행사 키워드 뉴스 검색 (+선택 RSS)

2026-07 도메인 전환 2단계: AI/LLM 개발 생태계(릴리즈/CVE/블로그) →
SkillRadar 교육·커리어 대상으로 완전 대체. 키워드/RSS 기본값은
SkillRadar app/core/config.py 의 기본값과 동기를 유지한다.
"""

from ..base import BrandConfig, BrandFeature

TENANT_ID = "tech-briefing"
DISPLAY_NAME = "TechBriefing AI 학습·커리어 일일 브리핑"
EMAIL_SUBJECT_PREFIX = "[TechBriefing]"
EMAIL_TEMPLATE = "tech_briefing/daily_report.html"


# ─── 수집 키워드/RSS — SkillRadar 기본값과 동기 유지 ────────────────
# (SkillRadar: NEWS_KEYWORDS / POLICY_KEYWORDS / POLICY_RSS_FEEDS / COURSE_KEYWORDS)
NEWS_KEYWORDS: list[str] = [
    "AI 교육",
    "AI 부트캠프",
    "인공지능 강의",
    "생성형 AI 교육",
]

POLICY_KEYWORDS: list[str] = [
    "AI 인재양성",
    "인공지능 정책",
    "디지털 직업훈련",
    "K-디지털",
]

# (label, url) — 정부 보도자료. korea.kr 은 키워드 1차 필터 후 사용.
# 주의: /rss/policy.xml 은 2026-07 현재 404 — 전체 정책 피드(policy_all)만 유효.
POLICY_RSS_FEEDS: list[tuple[str, str]] = [
    ("정책브리핑", "https://www.korea.kr/rss/policy_all.xml"),
]

COURSE_KEYWORDS: list[str] = [
    "AI 부트캠프 모집",
    "KDT 국비지원",
    "AI 세미나",
    "AI 컨퍼런스",
]

# 교육/세미나 신뢰 RSS (운영자 확장 지점 — SkillRadar COURSE_RSS_FEEDS 대응).
COURSE_RSS_FEEDS: list[tuple[str, str]] = []

# 제목에 이 힌트가 있으면 course → seminar 로 분류 (SkillRadar course.py 와 동일).
SEMINAR_HINTS: list[str] = [
    "세미나", "컨퍼런스", "웨비나", "밋업", "포럼", "행사",
    "webinar", "conference",
]

# 제목에 이 힌트가 있으면 "모집·마감" 신호 — 스코어 가산 + 푸터 미니 리스트.
RECRUITING_HINTS: list[str] = [
    "모집", "신청", "접수", "마감", "선발", "지원자",
]

# 키워드당 검색 결과 상한 / 정책 RSS 파싱 상한.
MAX_PER_KEYWORD = 8
RSS_MAX_ITEMS = 30


# ─── 카테고리 → 한글 라벨/색상 ─────────────────────────────────────
CATEGORY_META: dict[str, dict[str, str]] = {
    "course":  {"label": "교육과정",   "color": "#15803d", "bg": "#dcfce7"},
    "seminar": {"label": "세미나·행사", "color": "#7c3aed", "bg": "#ede9fe"},
    "policy":  {"label": "정책·지원",   "color": "#b91c1c", "bg": "#fee2e2"},
    "news":    {"label": "뉴스",       "color": "#0e7490", "bg": "#cffafe"},
}

# 카테고리 가중치 — 실행 가능성(모집·지원) 높은 유형 우선.
CATEGORY_WEIGHT: dict[str, float] = {
    "policy":  1.5,
    "course":  1.2,
    "seminar": 1.0,
    "news":    0.8,
}

# Brand: blue/cyan 톤 유지 (템플릿 인라인 스타일과 결합)
BRAND_CONFIG = BrandConfig(
    primary_color="#1d4ed8",
    primary_color_dark="#1e3a8a",
    accent_color="#06b6d4",
    logo_text="TechBriefing",
    tagline="AI 학습·커리어 일일 브리핑",
    description="AI 교육과정·세미나·정책·뉴스를 매일 아침 이메일로 받아보세요",
    features=[
        BrandFeature(
            icon="&#x1F3AF;",
            title="오늘의 헤드라인",
            description="교육·세미나·정책·뉴스를 합성한 핵심 5건",
        ),
        BrandFeature(
            icon="&#x1F4E2;",
            title="모집·마감 임박",
            description="부트캠프 모집 / 세미나 신청 / 지원사업 마감 신호",
        ),
        BrandFeature(
            icon="&#x1F4C8;",
            title="키워드 트렌드",
            description="오늘의 상승 키워드와 동반 빈출 키워드",
        ),
        BrandFeature(
            icon="&#x1F3DB;",
            title="공식 출처 우선",
            description="정부 정책브리핑(korea.kr) + 뉴스 키워드 큐레이션",
        ),
    ],
)
