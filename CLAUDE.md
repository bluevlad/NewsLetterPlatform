# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
python -m src.main --run-once   # 1회 실행 (수집 → 발송)
python -m src.main --collect-only  # 수집만
python -m src.main --send-only     # 발송만

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

## Deployment

- **CI/CD**: GitHub Actions (prod 브랜치 push 시 자동 배포)
- **네트워크**: unmong-network (외부)
- **운영 포트**: 4050

> 로컬 환경 정보는 `CLAUDE.local.md` 참조 (git에 포함되지 않음)
