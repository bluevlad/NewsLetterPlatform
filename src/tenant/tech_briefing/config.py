"""TechBriefing 테넌트 설정 — AI 학습·커리어 일일 브리핑.

데이터 소스: SkillRadar 백엔드(9070) 뉴스레터 공급 API (collector 참조).
수집 키워드/RSS 소스 관리는 SkillRadar admin(source_configs)으로 일원화 —
과거 이 파일에 있던 키워드/RSS 복제 상수는 Phase 2 에서 제거됐다.
여기에는 플랫폼 측 편성(스코어링·분류·브랜드) 설정만 남긴다.
"""

from ..base import BrandConfig, BrandFeature

TENANT_ID = "tech-briefing"
DISPLAY_NAME = "TechBriefing AI 학습·커리어 일일 브리핑"
EMAIL_SUBJECT_PREFIX = "[TechBriefing]"
EMAIL_TEMPLATE = "tech_briefing/daily_report.html"


# 제목에 이 힌트가 있으면 course → seminar 로 분류 (SkillRadar course.py 와 동일).
# 수집 경로에서는 SkillRadar 가 분류해서 내려주므로 재분류 유틸/테스트용.
SEMINAR_HINTS: list[str] = [
    "세미나", "컨퍼런스", "웨비나", "밋업", "포럼", "행사",
    "webinar", "conference",
]

# 제목에 이 힌트가 있으면 "모집·마감" 신호 — 스코어 가산 + 푸터 미니 리스트.
RECRUITING_HINTS: list[str] = [
    "모집", "신청", "접수", "마감", "선발", "지원자",
]


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


# ─────────────────────────────────────────────────────────────
# 테넌트 환경 설정 (P1b, 2026-08-15) — 전역 config.py 에서 이관.
# env 변수명은 필드명 대문자화로 매칭 (기존 변수명 그대로 유지).
# ollama_base_url / llmops_* 는 플랫폼 공유 인프라라 전역에 남김.
# ─────────────────────────────────────────────────────────────

from pathlib import Path

from pydantic_settings import BaseSettings


class TechBriefingSettings(BaseSettings):
    """TechBriefing 테넌트 환경 설정 (.env 공유 로드)"""

    # Daily 스케줄 — 발송은 슬롯이 결정, SEND_HOUR/MINUTE 는 deprecated 호환 유지
    tech_collect_hour: int = 6
    tech_collect_minute: int = 30
    tech_send_hour: int = 8
    tech_send_minute: int = 0

    # Weekly (매주 금요일 — _get_period_range 가 월~오늘 집계라 금요일 발송)
    tech_weekly_day_of_week: str = "fri"
    tech_weekly_collect_hour: int = 6
    tech_weekly_collect_minute: int = 0
    tech_weekly_send_hour: int = 9
    tech_weekly_send_minute: int = 30

    # SkillRadar Backend — 뉴스레터 공급 API (X-Newsletter-Key)
    skillradar_api_url: str = "http://host.docker.internal:9070"
    skillradar_newsletter_key: str = ""  # 빈 값이면 수집 스킵

    # Ollama LLM deep analyzer (모델·호출 파라미터. base URL 은 전역 공유)
    tech_briefing_llm_enabled: bool = True
    tech_briefing_llm_model: str = "exaone3.5:7.8b"
    tech_briefing_llm_timeout_sec: int = 90
    tech_briefing_llm_top_n: int = 5
    tech_briefing_llm_temperature: float = 0.2
    tech_briefing_consumer_id: str = "tech-briefing-newsletter"

    class Config:
        env_file = Path(__file__).resolve().parents[3] / ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


tenant_settings = TechBriefingSettings()
