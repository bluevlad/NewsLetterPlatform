# NewsLetterPlatform

멀티테넌트 뉴스레터 통합 플랫폼.

## Tech Stack

- Python 3.12+ / FastAPI / APScheduler
- SQLite (SQLAlchemy 2.0)
- Gmail SMTP / Jinja2
- Docker Compose

## Quick Start

```bash
python -m venv venv
source venv/bin/activate     # Linux/Mac
venv\Scripts\activate        # Windows

pip install -r requirements.txt

cp .env.example .env
# .env 파일 편집

python -m src.main --web        # 웹 서버
python -m src.main              # 스케줄러
python -m src.main --run-once   # 1회 실행 (수집 + 발송)
```

## Docker

```bash
docker compose up -d
docker compose -f docker-compose.prod.yml up -d   # 운영
```

기본 포트: `4050`

## License

Private
