# NewsLetter Platform - Architecture Plan

## Overview

모든 에이전트의 뉴스레터 기능을 단일 멀티테넌트 플랫폼으로 통합 관리하는 서비스.
신규 프로젝트로 구현 후, 기존 AllergyInsight 뉴스레터(포트 4050)를 통합 예정.

## Service Info

| 항목 | 값 |
|------|-----|
| 서비스명 | NewsLetterPlatform |
| 포트 | 4055 (Phase 1) → 4050 (Phase 2, AllergyInsight NL 포트 승계) |
| 도메인 | www.unmong.com |
| 네트워크 | unmong-network |

## Tenant (에이전트) 목록

| Tenant | 상태 | 비고 |
|--------|------|------|
| AllergyInsight | 통합 예정 | 기존 독립 서비스(4050) → 플랫폼으로 마이그레이션 |
| TeacherHub | 신규 개발 | |
| AcademyInsight | 신규 개발 | |
| HealthPulse | 기존 서비스 확인 후 통합 검토 | |

## URL Routing

```
/allergy-insight/*    → AllergyInsight 뉴스레터
/teacher-hub/*        → TeacherHub 뉴스레터
/academy-insight/*    → AcademyInsight 뉴스레터
/health-pulse/*       → HealthPulse 뉴스레터
```

## Core Modules (공통 기능)

```
newsletter-platform/
├── common/
│   ├── subscription/    # 구독 관리 (구독/해지/목록)
│   ├── delivery/        # 이메일 발송 엔진
│   ├── template/        # 뉴스레터 템플릿 렌더링
│   └── scheduler/       # 정기 발송 스케줄러
├── tenant/
│   ├── allergy-insight/ # 테넌트별 설정, 템플릿, 데이터 소스
│   ├── teacher-hub/
│   ├── academy-insight/
│   └── health-pulse/
└── admin/               # 통합 관리 콘솔
```

## Migration Plan

```
Phase 1: 신규 플랫폼 구축 (TeacherHub, AcademyInsight 뉴스레터)
Phase 2: 기존 AllergyInsight 뉴스레터(4050) 통합 마이그레이션
Phase 3: HealthPulse 및 향후 에이전트 통합
```

## Integration Point

- 각 에이전트 메인 서비스 → REST API로 뉴스레터 플랫폼 호출
- unmong-main 포털 → 테넌트별 뉴스레터 URL로 링크
