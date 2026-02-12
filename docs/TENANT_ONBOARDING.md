# 테넌트 온보딩 가이드

새 테넌트를 NewsLetterPlatform에 추가하기 위한 표준 절차입니다.

## 1. 사전 요구사항

- 원본 서비스의 REST API가 준비되어 있어야 합니다
- API 엔드포인트 목록, 인증 방식, 응답 스키마를 확인합니다
- 테넌트 ID를 결정합니다 (소문자 kebab-case, 예: `allergy-insight`)

## 2. 디렉토리 구조 생성

```
src/tenant/{tenant_name}/
├── __init__.py       # 테넌트 클래스 (BaseTenant 상속)
├── config.py         # 설정 상수 (TENANT_ID, BRAND_CONFIG 등)
├── collector.py      # 데이터 수집기
└── formatter.py      # 리포트 포매터
```

```bash
mkdir -p src/tenant/{tenant_name}
touch src/tenant/{tenant_name}/__init__.py
touch src/tenant/{tenant_name}/config.py
touch src/tenant/{tenant_name}/collector.py
touch src/tenant/{tenant_name}/formatter.py
```

## 3. config.py 설정

```python
"""
{TenantName} 테넌트 설정
"""

from ..base import BrandConfig, BrandFeature

TENANT_ID = "{tenant-id}"
DISPLAY_NAME = "{TenantName} 뉴스레터 제목"
EMAIL_SUBJECT_PREFIX = "[{TenantName}]"
EMAIL_TEMPLATE = "{tenant_name}/daily_report.html"

BRAND_CONFIG = BrandConfig(
    primary_color="#??????",
    primary_color_dark="#??????",
    accent_color="#??????",
    logo_text="{TenantName}",
    tagline="한줄 소개",
    description="index.html 카드에 표시될 설명",
    features=[
        BrandFeature(
            icon="&#x1F4CA;",       # HTML 엔티티 아이콘
            title="기능 제목",
            description="기능 설명",
        ),
        # 2~3개 권장
    ],
)
```

## 4. 브랜드 색상 가이드

### 색상 팔레트 기준표

| 테넌트 | Primary | Primary Dark | Accent | 계열 |
|--------|---------|-------------|--------|------|
| TeacherHub | `#3b82f6` | `#2563eb` | `#60a5fa` | Blue |
| AcademyInsight | `#8b5cf6` | `#7c3aed` | `#a78bfa` | Violet |
| _(예약) AllergyInsight_ | `#10b981` | `#059669` | `#34d399` | Emerald |
| _(예약) HealthPulse_ | `#f59e0b` | `#d97706` | `#fbbf24` | Amber |

### 색상 선택 권장사항

- **primary_color**: 버튼 그라데이션 시작색. Tailwind 500 톤 권장
- **primary_color_dark**: 버튼 그라데이션 끝색. Tailwind 600 톤 권장
- **accent_color**: 링크, 포커스 강조. Tailwind 400 톤 권장
- 기존 테넌트와 색상 계열이 겹치지 않도록 선택합니다
- 다크 배경(#0f172a)에서 가독성이 좋은 색상을 선택합니다

## 5. collector.py 구현 패턴

```python
"""
{TenantName} 데이터 수집기
"""

import logging
from typing import Any, Dict

import httpx

from ...config import settings

logger = logging.getLogger(__name__)


class {TenantName}Collector:
    """원본 서비스 API에서 데이터를 수집"""

    def __init__(self):
        self.base_url = settings.{tenant_name}_api_url
        self.timeout = httpx.Timeout(30.0)

    async def collect_all(self) -> Dict[str, Any]:
        """전체 데이터 수집"""
        data = {}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            # 각 API 엔드포인트에서 데이터 수집
            data["summary"] = await self._collect_summary(client)
            # ... 필요한 데이터 추가
        return data

    async def _collect_summary(self, client: httpx.AsyncClient) -> Any:
        """요약 데이터 수집"""
        try:
            response = await client.get(f"{self.base_url}/api/summary")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"요약 데이터 수집 실패: {e}")
            return None
```

## 6. formatter.py 구현 패턴

```python
"""
{TenantName} 리포트 포매터
"""

from typing import Any, Dict


class {TenantName}Formatter:
    """수집 데이터를 이메일 템플릿 변수로 변환"""

    def format(self, collected_data: Dict[str, Any]) -> Dict[str, Any]:
        """템플릿 컨텍스트 생성"""
        return {
            "summary": collected_data.get("summary"),
            # ... 템플릿에 필요한 변수 매핑
        }
```

## 7. __init__.py 테넌트 클래스

```python
"""
{TenantName} 테넌트 구현
"""

from typing import Any, Dict

from ..base import BaseTenant, BrandConfig
from .config import TENANT_ID, DISPLAY_NAME, EMAIL_SUBJECT_PREFIX, EMAIL_TEMPLATE, BRAND_CONFIG
from .collector import {TenantName}Collector
from .formatter import {TenantName}Formatter
from ...config import settings


class {TenantName}Tenant(BaseTenant):

    def __init__(self):
        self._collector = {TenantName}Collector()
        self._formatter = {TenantName}Formatter()

    @property
    def tenant_id(self) -> str:
        return TENANT_ID

    @property
    def display_name(self) -> str:
        return DISPLAY_NAME

    @property
    def email_subject_prefix(self) -> str:
        return EMAIL_SUBJECT_PREFIX

    @property
    def email_template(self) -> str:
        return EMAIL_TEMPLATE

    @property
    def brand_config(self) -> BrandConfig:
        return BRAND_CONFIG

    @property
    def schedule_config(self) -> Dict[str, int]:
        return {
            "collect_hour": settings.{tenant_name}_collect_hour,
            "collect_minute": settings.{tenant_name}_collect_minute,
            "send_hour": settings.{tenant_name}_send_hour,
            "send_minute": settings.{tenant_name}_send_minute,
        }

    async def collect_data(self) -> Dict[str, Any]:
        return await self._collector.collect_all()

    def format_report(self, collected_data: Dict[str, Any]) -> Dict[str, Any]:
        return self._formatter.format(collected_data)
```

## 8. 이메일 템플릿 작성

`templates/{tenant_name}/daily_report.html` 파일을 생성합니다.

- 기존 테넌트 템플릿(`templates/teacher_hub/daily_report.html`)을 참고합니다
- 테넌트 브랜드 색상을 이메일 템플릿에도 반영합니다
- 이메일 클라이언트 호환성을 위해 인라인 CSS를 사용합니다

## 9. 등록 및 환경변수 설정

### 테넌트 레지스트리 등록

`src/tenant/registry.py`에서 새 테넌트를 import하고 등록합니다.

### 환경변수 추가 (.env)

```env
# {TenantName} 설정
{TENANT_NAME}_API_URL=http://172.30.1.72:PORT
{TENANT_NAME}_COLLECT_HOUR=7
{TENANT_NAME}_COLLECT_MINUTE=20
{TENANT_NAME}_SEND_HOUR=8
{TENANT_NAME}_SEND_MINUTE=20
```

### config.py 설정 추가

`src/config.py`의 Settings 클래스에 새 테넌트 관련 설정을 추가합니다.

### config/tenants.yaml 업데이트

새 테넌트 정보를 `config/tenants.yaml`에 추가합니다 (참조 문서용).

## 10. 테스트 체크리스트

- [ ] 웹 서버 시작 시 오류 없음 (`python -m src.main --web`)
- [ ] 랜딩 페이지(`/`)에서 새 테넌트 카드가 표시됨
- [ ] 테넌트 카드에 고유 색상 인디케이터가 적용됨
- [ ] 구독 페이지(`/{tenant-id}/subscribe`)에서 테넌트 테마 색상 적용됨
- [ ] 구독 페이지에 테넌트 features가 표시됨
- [ ] 구독 신청 → 인증코드 → 완료 플로우 정상 동작
- [ ] 구독 해지 플로우 정상 동작
- [ ] 데이터 수집 정상 동작 (`python -m src.main --collect-only`)
- [ ] 이메일 발송 정상 동작 (`python -m src.main --send-only`)
- [ ] 기존 테넌트 기능에 영향 없음
