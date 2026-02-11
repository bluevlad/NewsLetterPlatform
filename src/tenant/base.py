"""
테넌트 기본 인터페이스 (ABC)
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


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
