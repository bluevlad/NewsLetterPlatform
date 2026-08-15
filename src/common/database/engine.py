"""DB 엔진 수명주기 — init_db / 세션 팩토리 / SQLite PRAGMA.

P1b(2026-08-15): repository.py 분해로 분리. 엔진·세션 전역은 이 모듈이 소유한다.
"""

import logging
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from .models import Base
from .migrations import run_all_migrations

logger = logging.getLogger(__name__)

_engine = None
_SessionLocal = None


def init_db(database_url: str = "sqlite:///./data/newsletterplatform.db") -> None:
    """데이터베이스 초기화"""
    global _engine, _SessionLocal

    if database_url.startswith("sqlite:///"):
        db_path = database_url.replace("sqlite:///", "")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    _engine = create_engine(
        database_url,
        echo=False,
        connect_args={"check_same_thread": False} if "sqlite" in database_url else {}
    )

    if "sqlite" in database_url:
        # web·scheduler 두 프로세스가 같은 파일에 동시 쓰기하므로
        # WAL(reader-writer 비차단) + busy_timeout(락 대기) 필수.
        # 미설정 시 발송 트랜잭션과 겹친 쓰기가 'database is locked'로 즉사한다.
        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=15000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

    Base.metadata.create_all(bind=_engine)

    run_all_migrations(_engine)


@contextmanager
def get_session():
    """세션 컨텍스트 매니저"""
    if _SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")

    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session_factory():
    """세션 팩토리 반환 (웹 앱에서 사용)"""
    if _SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _SessionLocal


