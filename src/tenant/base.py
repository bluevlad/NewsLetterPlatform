"""
테넌트 기본 인터페이스 (ABC)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class BrandFeature:
    """구독 페이지에 표시할 기능 소개 항목"""
    icon: str
    title: str
    description: str


@dataclass
class BrandConfig:
    """테넌트 브랜드 설정"""
    primary_color: str = "#10b981"
    primary_color_dark: str = "#059669"
    accent_color: str = "#38bdf8"
    logo_text: str = "NewsLetterPlatform"
    tagline: str = "멀티테넌트 뉴스레터 통합 플랫폼"
    description: str = "매일 분석 데이터를 이메일로 받아보세요"
    features: List[BrandFeature] = field(default_factory=list)


class BaseTenant(ABC):
    """멀티테넌트 기본 클래스"""

    @property
    @abstractmethod
    def tenant_id(self) -> str:
        """테넌트 고유 식별자 (예: 'teacher-hub')"""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """표시용 이름 (예: 'TeacherHub 강사 평판 브리핑')"""

    @property
    @abstractmethod
    def email_subject_prefix(self) -> str:
        """이메일 제목 접두사 (예: '[TeacherHub]')"""

    @property
    @abstractmethod
    def email_template(self) -> str:
        """이메일 템플릿 경로 (예: 'teacher_hub/daily_report.html')"""

    @property
    @abstractmethod
    def schedule_config(self) -> Dict[str, int]:
        """스케줄러 설정 (collect_hour, collect_minute, send_hour, send_minute)"""

    @property
    def brand_config(self) -> BrandConfig:
        """테넌트 브랜드 설정 (하위 클래스에서 오버라이드 가능)"""
        return BrandConfig()

    @abstractmethod
    async def collect_data(self) -> Dict[str, Any]:
        """데이터 수집 (원본 서비스 API 호출)

        Returns:
            수집된 데이터 딕셔너리 (data_type: data)
        """

    @abstractmethod
    def format_report(self, collected_data: Dict[str, Any]) -> Dict[str, Any]:
        """수집 데이터를 템플릿 변수로 변환

        Returns:
            템플릿 렌더링에 사용할 컨텍스트 딕셔너리
        """

    def generate_subject(self, report_date=None) -> str:
        """이메일 제목 생성"""
        from datetime import datetime
        if report_date is None:
            report_date = datetime.now()
        date_str = report_date.strftime("%Y-%m-%d")
        return f"{self.email_subject_prefix} {date_str} 일일 브리핑"
