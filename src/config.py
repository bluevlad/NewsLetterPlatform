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

    # 스케줄러 - AllergyInsight (Daily)
    # 발송은 슬롯(early 6:40 / mid 7:40 / late 8:40)별로 분기되며,
    # SEND_HOUR/MINUTE는 deprecated이지만 호환성 위해 유지.
    # COLLECT는 가장 빠른 슬롯(6:40)보다 충분히 일찍 5:00 단일 실행.
    allergy_collect_hour: int = Field(default=5, env="ALLERGY_COLLECT_HOUR")
    allergy_collect_minute: int = Field(default=0, env="ALLERGY_COLLECT_MINUTE")
    allergy_send_hour: int = Field(default=8, env="ALLERGY_SEND_HOUR")
    allergy_send_minute: int = Field(default=30, env="ALLERGY_SEND_MINUTE")

    # 스케줄러 - AllergyInsight (Weekly: 매주 금요일)
    allergy_weekly_day_of_week: str = Field(default="fri", env="ALLERGY_WEEKLY_DAY_OF_WEEK")
    allergy_weekly_collect_hour: int = Field(default=5, env="ALLERGY_WEEKLY_COLLECT_HOUR")
    allergy_weekly_collect_minute: int = Field(default=0, env="ALLERGY_WEEKLY_COLLECT_MINUTE")
    allergy_weekly_send_hour: int = Field(default=9, env="ALLERGY_WEEKLY_SEND_HOUR")
    allergy_weekly_send_minute: int = Field(default=30, env="ALLERGY_WEEKLY_SEND_MINUTE")

    # 스케줄러 - AllergyInsight (Monthly: 매월 말일)
    allergy_monthly_day_of_month: str = Field(default="last", env="ALLERGY_MONTHLY_DAY_OF_MONTH")
    allergy_monthly_collect_hour: int = Field(default=5, env="ALLERGY_MONTHLY_COLLECT_HOUR")
    allergy_monthly_collect_minute: int = Field(default=0, env="ALLERGY_MONTHLY_COLLECT_MINUTE")
    allergy_monthly_send_hour: int = Field(default=10, env="ALLERGY_MONTHLY_SEND_HOUR")
    allergy_monthly_send_minute: int = Field(default=0, env="ALLERGY_MONTHLY_SEND_MINUTE")

    # 테넌트 API URLs
    allergy_insight_api_url: str = Field(
        default="http://localhost:9040",
        env="ALLERGY_INSIGHT_API_URL"
    )
    allergy_insight_admin_name: str = Field(
        default="",
        env="ALLERGY_INSIGHT_ADMIN_NAME"
    )
    allergy_insight_admin_phone: str = Field(
        default="",
        env="ALLERGY_INSIGHT_ADMIN_PHONE"
    )
    allergy_insight_admin_pin: str = Field(
        default="",
        env="ALLERGY_INSIGHT_ADMIN_PIN"
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

    class Config:
        env_file = Path(__file__).parent.parent / ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    """설정 싱글톤 반환"""
    return Settings()


settings = get_settings()
