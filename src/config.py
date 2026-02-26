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

    # 스케줄러 - TeacherHub
    teacherhub_collect_hour: int = Field(default=7, env="TEACHERHUB_COLLECT_HOUR")
    teacherhub_collect_minute: int = Field(default=0, env="TEACHERHUB_COLLECT_MINUTE")
    teacherhub_send_hour: int = Field(default=8, env="TEACHERHUB_SEND_HOUR")
    teacherhub_send_minute: int = Field(default=0, env="TEACHERHUB_SEND_MINUTE")

    # 스케줄러 - AcademyInsight
    academy_collect_hour: int = Field(default=7, env="ACADEMY_COLLECT_HOUR")
    academy_collect_minute: int = Field(default=10, env="ACADEMY_COLLECT_MINUTE")
    academy_send_hour: int = Field(default=8, env="ACADEMY_SEND_HOUR")
    academy_send_minute: int = Field(default=10, env="ACADEMY_SEND_MINUTE")

    # 스케줄러 - EduFit
    edufit_collect_hour: int = Field(default=7, env="EDUFIT_COLLECT_HOUR")
    edufit_collect_minute: int = Field(default=20, env="EDUFIT_COLLECT_MINUTE")
    edufit_send_hour: int = Field(default=8, env="EDUFIT_SEND_HOUR")
    edufit_send_minute: int = Field(default=20, env="EDUFIT_SEND_MINUTE")

    # 테넌트 API URLs
    teacherhub_api_url: str = Field(
        default="http://localhost:8081/api/v2",
        env="TEACHERHUB_API_URL"
    )
    academy_insight_api_url: str = Field(
        default="http://localhost:8082/api",
        env="ACADEMY_INSIGHT_API_URL"
    )
    edufit_api_url: str = Field(
        default="http://localhost:9070/api/v1",
        env="EDUFIT_API_URL"
    )

    # 웹 서버
    web_host: str = Field(default="0.0.0.0", env="WEB_HOST")
    web_port: int = Field(default=4055, env="WEB_PORT")
    web_base_url: str = Field(default="http://localhost:4055", env="WEB_BASE_URL")

    # 로깅
    log_level: str = Field(default="INFO", env="LOG_LEVEL")

    # 이메일 인증
    verification_code_length: int = Field(default=6)
    verification_expiry_minutes: int = Field(default=10)
    max_verification_attempts: int = Field(default=5)

    class Config:
        env_file = Path(__file__).parent.parent / ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    """설정 싱글톤 반환"""
    return Settings()


settings = get_settings()
