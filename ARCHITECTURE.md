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

## 구독 기능 구현 플랜

### 현재 상태 (구현 완료)

| 항목 | 상태 | 파일 |
|------|------|------|
| DB 모델 (subscribers, email_verifications) | 완료 | `src/common/database/models.py` |
| 구독 관리 매니저 (신청/인증/해지) | 완료 | `src/common/subscription/manager.py` |
| 이메일 인증 코드 발송 | 완료 | `src/common/subscription/email_service.py` |
| 웹 UI - 구독 신청 폼 | 완료 | `src/web/templates/subscribe.html` |
| 웹 UI - 인증코드 입력 | 완료 | `src/web/templates/verify_code.html` |
| 웹 UI - 구독 완료 페이지 | 완료 | `src/web/templates/result.html` |
| 웹 UI - 구독 해지 폼 | 완료 | `src/web/templates/unsubscribe_*.html` |
| 토큰 기반 구독 해지 (이메일 링크) | 완료 | `src/web/app.py` |
| 인증 이메일 템플릿 | 완료 | `templates/verification_code.html` |
| 랜딩 페이지 (테넌트 목록) | 완료 | `src/web/templates/index.html` |

### TODO (미구현)

#### Phase 1-1: 뉴스레터 이메일에 구독 해지 링크 추가
- [ ] `templates/teacher_hub/daily_report.html` 푸터에 구독 해지 링크 추가
- [ ] `templates/academy_insight/daily_report.html` 푸터에 구독 해지 링크 추가
- [ ] 발송 시 subscriber별 unsubscribe_token을 템플릿 컨텍스트에 전달
- [ ] URL 형식: `http://www.unmong.com:4055/{tenant_id}/unsubscribe/token/{token}`

#### Phase 1-2: 외부 접근 설정
- [ ] unmong-main 리버스 프록시에 뉴스레터 포트(4055) 라우팅 추가
- [ ] 또는 Nginx로 `/newsletter/*` → `localhost:4055` 프록시
- [ ] 구독 페이지 URL: `http://www.unmong.com:4055/{tenant_id}/subscribe`

#### Phase 1-3: 기존 서비스 연동
- [ ] TeacherHub 프론트엔드에 뉴스레터 구독 버튼/링크 추가
- [ ] AcademyInsight 프론트엔드에 뉴스레터 구독 버튼/링크 추가
- [ ] unmong-main 포털에 뉴스레터 섹션 추가

#### Phase 1-4: 실 사용 검증 및 개선
- [ ] 구독/해지 플로우 E2E 테스트
- [ ] 이메일 인증 코드 만료 시간 조정 (현재 10분)
- [ ] 중복 발송 방지 로직 검증 (UTC vs KST 이슈 확인)
- [ ] 에러 핸들링 및 로깅 보강
