# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> 도메인/URL/포트 규칙: [Claude-Opus-bluevlad/standards/infrastructure/DOMAIN_MANAGEMENT.md](https://github.com/bluevlad/Claude-Opus-bluevlad/blob/main/standards/infrastructure/DOMAIN_MANAGEMENT.md) — `https://도메인:포트` 사용 금지
> 발송 유형 분리: [Claude-Opus-bluevlad/standards/newsletterplatform/SEND_TYPE_SEPARATION.md](https://github.com/bluevlad/Claude-Opus-bluevlad/blob/main/standards/newsletterplatform/SEND_TYPE_SEPARATION.md) — **메일 발송 관련 수정 시 반드시 참조**

## 실행 환경 감지 (SSH 재접속 금지)

- Claude는 현재 호스트에서 직접 실행 중 — **SSH 재접속을 시도하지 말 것**
- `uname -s` = `Darwin` → MacBook 운영환경 (172.30.1.72), docker/docker compose 직접 실행 가능
- `uname -s` 결과가 Windows/MINGW/MSYS → Windows 개발환경 (172.30.1.100)
- Docker 명령은 현재 호스트에서 바로 실행 (별도 SSH 접속 불필요)
- compose 파일 선택: Darwin → `docker-compose.yml` / Windows → `docker-compose.local.yml`

## Project Overview

멀티테넌트 뉴스레터 통합 플랫폼 - EduFit, AllergyInsight 뉴스레터 발송

## Environment

- **Database**: SQLite (SQLAlchemy ORM)
- **Target Server**: MacBook Docker (172.30.1.72) / Windows 로컬 개발
- **Docker Strategy**: Docker Compose (web + scheduler)
- **Python Version**: 3.12+

## Tech Stack

| 항목 | 기술 |
|------|------|
| Language | Python 3.12+ |
| Framework | FastAPI + Uvicorn |
| ORM | SQLAlchemy 2.0+ |
| Database | SQLite |
| Scheduler | APScheduler |
| Template | Jinja2 |
| Email | Gmail SMTP (aiosmtplib) |
| HTTP Client | httpx |
| Config | Pydantic + python-dotenv |
| Package Manager | pip |

## Setup and Run Commands

```bash
# 가상환경 생성 및 활성화
python -m venv venv
venv\Scripts\activate     # Windows
source venv/bin/activate  # Linux/Mac

# 의존성 설치
pip install -r requirements.txt

# 실행 모드
python -m src.main              # 스케줄러 모드 (기본)
python -m src.main --web        # 웹 서버 모드
python -m src.main --run-once   # 1회 실행 (수집 → 발송, manual 모드)
python -m src.main --collect-only  # 수집만
python -m src.main --send-only     # 발송만 (manual 모드 — 자동발송 이력에 영향 없음)

# Docker
docker compose up -d            # 개발
docker compose -f docker-compose.prod.yml up -d  # 운영

# 테스트
pytest tests/
```

Default server port: 4050

## Project Structure

```
NewsLetterPlatform/
├── src/
│   ├── main.py              # 엔트리포인트
│   ├── config.py            # Pydantic 설정
│   ├── common/              # 공통 모듈 (구독, 발송, 템플릿, 스케줄러, DB)
│   ├── tenant/              # 테넌트별 모듈 (collector, formatter)
│   │   ├── base.py          # 테넌트 인터페이스 (ABC)
│   │   ├── edufit/          # EduFit 테넌트 (AcademyInsight+TeacherHub 통합)
│   │   └── allergy_insight/ # AllergyInsight 테넌트 (HealthPulse 통합)
│   └── web/                 # FastAPI 웹 앱
├── templates/               # 이메일 HTML 템플릿
├── config/                  # tenants.yaml 등 설정 파일
├── tests/
├── data/                    # SQLite DB (자동 생성)
└── logs/
```

## Do NOT

- .env 파일 커밋 금지
- requirements.txt에 없는 패키지를 설치 없이 import 금지
- pydantic v1 문법과 v2 문법 혼용 금지 (v2 사용)
- 원본 서비스 DB에 직접 접근 금지 (반드시 REST API 통해서만)
- 서버 주소, 비밀번호 추측 금지 — 반드시 확인 후 사용
- 운영 Docker 컨테이너 직접 조작 금지

## Tenant Architecture

- 새 테넌트 추가 시: `src/tenant/{name}/` 디렉토리에 collector.py + formatter.py 구현
- 테넌트 인터페이스: `src/tenant/base.py`의 `BaseTenant` ABC 상속
- 데이터 수집: 원본 서비스 REST API → httpx 비동기 호출 → collected_data 테이블 캐싱

## Dependent Services

| 서비스 | 포트 | Docker 컨테이너명 |
|--------|------|-------------------|
| EduFit Backend | 9070 | edufit-backend |
| AllergyInsight Backend | 9040 | allergyinsight-backend |

## Configuration

- 환경변수는 `.env` 파일로 관리
- `.env` 로딩: pydantic-settings
- 테넌트 설정: `config/tenants.yaml`

## Help Page 관리

> 작성 표준: [HELP_PAGE_GUIDE.md](https://github.com/bluevlad/Claude-Opus-bluevlad/blob/main/standards/documentation/HELP_PAGE_GUIDE.md)
> HTML 템플릿: [help-page-template.html](https://github.com/bluevlad/Claude-Opus-bluevlad/blob/main/standards/documentation/templates/help-page-template.html)

- **기능 추가/변경/삭제 시 반드시 헬프 페이지도 함께 업데이트**
- 헬프 파일 위치: `src/web/static/help/`
- 서비스 accent-color: `#f59e0b` (Amber)
- 대상 가이드 파일:
  - `admin-guide.html` — 관리자 가이드 (테넌트 관리, 뉴스레터 발송)

## Deployment

- **CI/CD**: GitHub Actions (prod 브랜치 push 시 자동 배포)
- **네트워크**: unmong-network (외부)
- **운영 포트**: 4050

> 로컬 환경 정보는 `CLAUDE.local.md` 참조 (git에 포함되지 않음)

## Fix 커밋 오류 추적

> 상세: [FIX_COMMIT_TRACKING_GUIDE.md](https://github.com/bluevlad/Claude-Opus-bluevlad/blob/main/standards/git/FIX_COMMIT_TRACKING_GUIDE.md) | [ERROR_TAXONOMY.md](https://github.com/bluevlad/Claude-Opus-bluevlad/blob/main/standards/git/ERROR_TAXONOMY.md)

`fix:` 커밋 시 footer에 오류 추적 메타데이터를 **필수** 포함합니다.

### 이 프로젝트에서 자주 발생하는 Root-Cause

| Root-Cause | 설명 | 예방 |
|-----------|------|------|
| `env-assumption` | Docker 내/외부 경로, 환경변수 가정 | Settings 클래스에서 필수값 검증, 기본값 금지 |
| `import-error` | 패키지 import 경로 오류, 상대/절대 경로 혼동 | `__init__.py` 확인, 절대 import 사용 |
| `null-handling` | Optional 필드 None 미처리 | Pydantic `Optional[T]` + 기본값 명시 |
| `type-mismatch` | SQLAlchemy 모델 ↔ Pydantic 스키마 타입 불일치 | `model_validate()` 사용, from_attributes=True |
| `async-handling` | await 누락, 동기/비동기 혼용 | async def에서 동기 DB 호출 금지, run_in_executor 사용 |
| `db-migration` | Alembic 마이그레이션 누락/충돌 | 스키마 변경 시 반드시 `alembic revision --autogenerate` |

### 예시

```
fix(api): 알레르기 성분 조회 시 None 응답 처리

- ingredient가 Optional인데 None 체크 없이 .name 접근하여 AttributeError 발생
- None일 때 빈 문자열 반환하도록 수정

Root-Cause: null-handling
Error-Category: logic-error
Affected-Layer: backend/api
Recurrence: first
Prevention: Optional 필드 접근 전 반드시 None 체크, or 연산자 활용

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
```
