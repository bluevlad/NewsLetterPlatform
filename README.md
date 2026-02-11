# NewsLetterPlatform

멀티테넌트 뉴스레터 통합 플랫폼

각 에이전트 서비스(TeacherHub, AcademyInsight 등)의 분석 데이터를 뉴스레터로 자동 발송합니다.

## Tech Stack

- Python 3.12+ / FastAPI / APScheduler
- SQLite (SQLAlchemy 2.0)
- Gmail SMTP / Jinja2
- Docker Compose

## Quick Start

```bash
# 가상환경
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # Linux/Mac

# 의존성 설치
pip install -r requirements.txt

# 환경변수 설정
cp .env.example .env
# .env 파일 편집

# 실행
python -m src.main --web       # 웹 서버
python -m src.main             # 스케줄러
python -m src.main --run-once  # 1회 실행
```

## Docker

```bash
docker compose up -d           # 개발
docker compose -f docker-compose.prod.yml up -d  # 운영
```

## Ports

| Phase | Port | 비고 |
|-------|------|------|
| Phase 1 | 4055 | TeacherHub + AcademyInsight 뉴스레터 |
| Phase 2 | 4050 | AllergyInsight 마이그레이션 후 승계 |

## License

Private
