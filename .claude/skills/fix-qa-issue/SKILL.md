---
name: fix-qa-issue
description: QA Agent 이슈 자동 수정 워크플로 — Auto-Tobe-Agent가 headless로 호출. 프로젝트별 수정 규칙/빌드/테스트 명령 포함.
---

# NewsLetterPlatform QA 이슈 수정 규칙

Auto-Tobe-Agent(Fixer)가 GitHub Issue 자동 수정 시 이 스킬을 호출합니다.
이슈 컨텍스트(제목/문제/권장 수정/영향 파일)는 프롬프트로 전달됩니다.

## 프로젝트 구조

| 영역 | 경로 | 스택 |
|------|------|------|
| 공통 모듈 | `src/common/` | 구독/발송/템플릿/스케줄러/DB/security |
| 테넌트 | `src/tenant/{allergy_insight,tech_briefing,standup}/` | collector + formatter (BaseTenant 상속) |
| 웹 | `src/web/` | FastAPI + admin 패널 + Jinja2 SSR |
| 설정 | `config/tenants.yaml`, `config/service_profiles/` | |
| 테스트 | `tests/` | pytest |

- Python 3.12+ FastAPI, SQLAlchemy 2.0, APScheduler, aiosmtplib(Gmail SMTP)
- TechBriefing 테넌트는 SkillRadar 백엔드 API 소비 (`SKILLRADAR_*` 환경변수)

## 검증 명령

```bash
pytest tests/                        # 수정 후 필수
```

## 수정 규칙

- 기존 코드 스타일 유지, 필요한 최소한의 변경만 수행
- 새 의존성 추가 금지 (이슈가 명시할 때만)
- **금지 영역**: `.env*`, `src/common/security/`(abuse guard — captcha/rate-limit/honeypot 완화 금지), `src/web/admin/auth.py` 인증 완화
- **⚠️ SQLite → PostgreSQL 마이그레이션 진행 중**: DB 드라이버/DATABASE_URL/스키마 관련 코드는 보수적으로 — 이슈가 DB를 직접 지목하지 않으면 건드리지 말 것
- 발송(dedup/send_mode) 로직 변경 시 `sent_articles` 이력 호환성 유지
- 새 테넌트 추가/제거는 자동 수정 범위 밖 (사람 작업)

## 완료 보고

수정 완료 후 응답 마지막에 FIX-REPORT 블록을 출력하세요 (오케스트레이터가 커밋 footer 생성에 사용):

```
<!-- FIX-REPORT
{"summary": "...", "rootCause": "...", "errorCategory": "...", "affectedLayer": "backend/tenant", "prevention": "..."}
-->
```

**커밋하지 마세요** — 파일 수정만 수행합니다 (커밋/PR은 오케스트레이터 담당).
