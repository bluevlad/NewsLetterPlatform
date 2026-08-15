"""
NewsLetterPlatform 설정 관리 모듈
"""

from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache


class Settings(BaseSettings):
    """애플리케이션 설정"""

    # 프로젝트 경로
    BASE_DIR: Path = Path(__file__).parent.parent

    # Gmail SMTP
    gmail_address: str = Field(default="", env="GMAIL_ADDRESS")
    gmail_app_password: str = Field(default="", env="GMAIL_APP_PASSWORD")

    # 데이터베이스
    database_url: str = Field(
        default="sqlite:///./data/newsletterplatform.db",
        env="DATABASE_URL"
    )

    # 테넌트별 스케줄·API·인증 설정은 각 테넌트 패키지로 이관 (P1b, 2026-08-15):
    #   src/tenant/{allergy_insight,standup,tech_briefing}/config.py 의
    #   TenantSettings — env 변수명은 기존 그대로 유지.
    # 여기에는 플랫폼 공유 인프라 설정만 남긴다.

    # Ollama — StandUp 과 공유하는 로컬 LLM 인프라 (테넌트 공용)
    # localhost:11434 (개발/macOS) · host.docker.internal:11434 (Docker 컨테이너)
    ollama_base_url: str = Field(
        default="http://localhost:11434", env="OLLAMA_BASE_URL"
    )

    # 웹 서버
    web_host: str = Field(default="0.0.0.0", env="WEB_HOST")
    web_port: int = Field(default=4050, env="WEB_PORT")
    web_base_url: str = Field(default="http://localhost:4050", env="WEB_BASE_URL")
    root_path: str = Field(default="", env="ROOT_PATH")

    # 로깅
    log_level: str = Field(default="INFO", env="LOG_LEVEL")

    # 이메일 인증
    verification_code_length: int = Field(default=6)
    verification_expiry_minutes: int = Field(default=10)
    max_verification_attempts: int = Field(default=5)

    # CSRF 허용 호스트 (쉼표 구분)
    csrf_allowed_hosts: str = Field(default="", env="CSRF_ALLOWED_HOSTS")

    # Admin
    admin_password: str = Field(default="", env="ADMIN_PASSWORD")
    admin_session_hours: int = Field(default=24, env="ADMIN_SESSION_HOURS")
    # 관리자 세션 서명 시크릿 (안정값). 설정 시 세션이 재시작·다중 워커에서 유지된다.
    # 비어 있으면 기동 시 임의 생성(기존 동작 — 재시작 시 세션 소실). 운영은 반드시 설정.
    # 생성: openssl rand -hex 32
    session_secret: str = Field(default="", env="SESSION_SECRET")

    # 리버스 프록시 신뢰 홉 수 — X-Forwarded-For 오른쪽에서 N번째(신뢰 프록시가 본 IP).
    # 게이트웨이 nginx 1단이면 1. 앞단에 CDN(Cloudflare 등)이 있으면 2.
    trusted_proxy_hops: int = Field(default=1, env="TRUSTED_PROXY_HOPS")

    # Google Sign-In (Admin 로그인용 - client_id만 필요)
    google_client_id: str = Field(default="", env="GOOGLE_CLIENT_ID")
    super_admin_emails: str = Field(default="", env="SUPER_ADMIN_EMAILS")

    # 구독 폼 어뷰즈 방어 (2026-05-02 Subscription Bombing 대응)
    # Cloudflare Turnstile — site/secret key 비어 있으면 captcha 비활성화 (개발/테스트용)
    turnstile_site_key: str = Field(default="", env="TURNSTILE_SITE_KEY")
    turnstile_secret_key: str = Field(default="", env="TURNSTILE_SECRET_KEY")
    # IP 기반 rate limit — slowapi 표기법. 변경 시 어뷰즈 baseline 재산정 필요
    subscribe_rate_limit_ip: str = Field(default="5/hour", env="SUBSCRIBE_RATE_LIMIT_IP")
    # 이메일 기반 — 동일 메일로 N분/N일 내 재발송 횟수 제한
    subscribe_rate_limit_email_minutes: int = Field(default=5, env="SUBSCRIBE_RATE_LIMIT_EMAIL_MINUTES")
    subscribe_rate_limit_email_per_day: int = Field(default=3, env="SUBSCRIBE_RATE_LIMIT_EMAIL_PER_DAY")

    # 휴일 발송 정책 — 공휴일(대체휴일 포함)은 holidays 패키지 KR 달력 기준.
    # extra_holidays: 회사 지정 휴무일 등 추가 휴일 (YYYY-MM-DD 콤마 구분).
    # 잘못된 형식 항목은 로그 경고 후 무시. 빈 값이면 법정 공휴일만 적용.
    extra_holidays: str = Field(default="", env="EXTRA_HOLIDAYS")
    # 휴일 관리자 테스트 발송 스위치 — false(기본)면 휴일(주말·공휴일) 자동 발송을
    # 전체 스킵. true면 기존 정책대로 early 슬롯에 SUPER_ADMIN_EMAILS 테스트 발송.
    holiday_admin_test_enabled: bool = Field(
        default=False, env="HOLIDAY_ADMIN_TEST_ENABLED"
    )

    # LLMOps 관측 보고 (BATCH_RUN_REPORTING v0.3.0, fire-and-forget)
    # 비어 있으면 보고 비활성. consumer_id 는 service-registry llm_consumers[].id 와 일치.
    llmops_enabled: bool = Field(default=False, env="LLMOPS_ENABLED")
    llmops_url: str = Field(
        default="http://host.docker.internal:9110/api/batch-runs", env="LLMOPS_URL"
    )
    llmops_api_key: str = Field(default="", env="LLMOPS_API_KEY")

    class Config:
        env_file = Path(__file__).parent.parent / ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    """설정 싱글톤 반환"""
    return Settings()


settings = get_settings()
