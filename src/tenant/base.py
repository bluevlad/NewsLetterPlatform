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


# 뉴스레터 유형별 라벨
NEWSLETTER_TYPE_LABELS = {
    "daily": "일일 브리핑",
    "weekly": "주간 분석 브리핑",
    "monthly": "월간 분석 브리핑",
}


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

    @property
    def supported_frequencies(self) -> List[str]:
        """지원하는 뉴스레터 주기 (기본: daily만)"""
        return ["daily"]

    @property
    def weekly_schedule_config(self) -> Dict[str, Any]:
        """주간 뉴스레터 스케줄 설정 (기본: 빈 dict)"""
        return {}

    @property
    def monthly_schedule_config(self) -> Dict[str, Any]:
        """월간 뉴스레터 스케줄 설정 (기본: 빈 dict)"""
        return {}

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

    async def collect_summary_data(self, newsletter_type: str,
                                    date_from=None, date_to=None) -> Dict[str, Any]:
        """주간/월간 요약 데이터 수집 (기본: 빈 dict)

        하위 클래스에서 오버라이드하여 기간별 데이터 수집 구현
        """
        return {}

    def format_summary_report(self, newsletter_type: str,
                               history_data: list,
                               collected_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """주간/월간 요약 데이터를 템플릿 변수로 변환 (기본: 빈 dict)

        Args:
            newsletter_type: "weekly" or "monthly"
            history_data: CollectedDataRepository.get_history_range() 결과
            collected_data: 추가 수집된 요약 데이터 (optional)
        """
        return {}

    def get_email_template(self, newsletter_type: str = "daily") -> str:
        """뉴스레터 유형별 템플릿 경로"""
        if newsletter_type == "daily":
            return self.email_template
        # 컨벤션: {tenant_dir}/weekly_report.html, monthly_report.html
        base_dir = self.email_template.rsplit("/", 1)[0]
        return f"{base_dir}/{newsletter_type}_report.html"

    def generate_subject(self, report_date=None, newsletter_type: str = "daily") -> str:
        """이메일 제목 생성"""
        from datetime import datetime
        if report_date is None:
            report_date = datetime.now()
        date_str = report_date.strftime("%Y-%m-%d")
        label = NEWSLETTER_TYPE_LABELS.get(newsletter_type, "일일 브리핑")
        return f"{self.email_subject_prefix} {date_str} {label}"
