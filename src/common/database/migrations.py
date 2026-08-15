"""스키마 마이그레이션 — 수제 idempotent ALTER 모음.

Alembic 미사용(플랫폼 표준 결정: SQLite 단일 파일 + PRAGMA 검사 기반 idempotent
ALTER). 각 함수는 컬럼/테이블 존재를 확인 후에만 변경하므로 재실행에 안전하다.
실패는 warning 으로 표면화한다 — "이미 적용됨"과 "진짜 실패"를 구분하기 위함
(과거 debug 레벨로 삼켜져 무음이던 문제 수정, 진단 M6).
"""

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _migrate_subscriber_send_slot(engine) -> None:
    """subscribers 테이블에 send_slot 컬럼 추가 + 기존 행을 'late'로 일괄 배정"""
    try:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(subscribers)"))
            columns = [row[1] for row in result]
            if "send_slot" not in columns:
                conn.execute(text(
                    "ALTER TABLE subscribers ADD COLUMN send_slot VARCHAR(20)"
                ))
                # 기존 구독자는 현재 발송 시간(8:20/8:30)에 가장 가까운 'late'(8:40)로 배정
                conn.execute(text(
                    "UPDATE subscribers SET send_slot = 'late' WHERE send_slot IS NULL"
                ))
                conn.commit()
                logger.info("subscribers 테이블에 send_slot 컬럼 추가 + 기존 행 'late' 일괄 배정 완료")
    except Exception as e:
        logger.warning(f"subscribers send_slot 마이그레이션 실패 — 스키마가 불완전할 수 있음: {e}")


def _migrate_subscriber_persona_columns(engine) -> None:
    """subscribers 테이블에 페르소나 적응형 4컬럼 추가 (N1).

    기존 행: persona_code/purpose/interests = NULL (런타임 'patient' 폴백),
    depth_level = 'practical' 기본값 적용. nullable/DEFAULT 추가만 — 무중단.
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(subscribers)"))
            columns = {row[1] for row in result}
            added = []
            if "persona_code" not in columns:
                conn.execute(text(
                    "ALTER TABLE subscribers ADD COLUMN persona_code VARCHAR(30)"
                ))
                added.append("persona_code")
            if "purpose" not in columns:
                conn.execute(text(
                    "ALTER TABLE subscribers ADD COLUMN purpose VARCHAR(50)"
                ))
                added.append("purpose")
            if "depth_level" not in columns:
                conn.execute(text(
                    "ALTER TABLE subscribers ADD COLUMN depth_level VARCHAR(20) DEFAULT 'practical'"
                ))
                added.append("depth_level")
            if "interests" not in columns:
                conn.execute(text(
                    "ALTER TABLE subscribers ADD COLUMN interests TEXT"
                ))
                added.append("interests")
            if added:
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_subscriber_tenant_persona "
                    "ON subscribers (tenant_id, persona_code, is_active)"
                ))
                conn.commit()
                logger.info(f"subscribers 페르소나 컬럼 추가 완료: {added}")
    except Exception as e:
        logger.warning(f"subscribers 페르소나 마이그레이션 실패 — 스키마가 불완전할 수 있음: {e}")


def _migrate_email_verification_signup_meta(engine) -> None:
    """email_verifications 테이블에 signup_meta(JSON) 컬럼 추가 (N1).

    구독 폼에서 고른 페르소나 선택을 인증 단계 너머로 운반하기 위함.
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(email_verifications)"))
            columns = {row[1] for row in result}
            if "signup_meta" not in columns:
                conn.execute(text(
                    "ALTER TABLE email_verifications ADD COLUMN signup_meta TEXT"
                ))
                conn.commit()
                logger.info("email_verifications 테이블에 signup_meta 컬럼 추가 완료")
    except Exception as e:
        logger.warning(f"email_verifications signup_meta 마이그레이션 실패 — 스키마가 불완전할 수 있음: {e}")


def _migrate_sent_articles_company_name(engine) -> None:
    """sent_articles 테이블에 company_name 컬럼 추가 + 기업명 인덱스.

    company-digest 일 단위 반복 노출을 차단하기 위해, 발송 기록을
    기업명 단위로도 조회 가능하게 한다.
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(sent_articles)"))
            columns = [row[1] for row in result]
            if "company_name" not in columns:
                conn.execute(text(
                    "ALTER TABLE sent_articles ADD COLUMN company_name VARCHAR(200)"
                ))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_sent_articles_company "
                    "ON sent_articles (tenant_id, company_name)"
                ))
                conn.commit()
                logger.info("sent_articles 테이블에 company_name 컬럼 + 인덱스 추가 완료")
    except Exception as e:
        logger.warning(f"sent_articles company_name 마이그레이션 실패 — 스키마가 불완전할 수 있음: {e}")


def _migrate_collection_metrics(engine) -> None:
    """collection_metrics 테이블/인덱스 idempotent 생성.

    Base.metadata.create_all 이 누락된 인덱스를 만들지 못하는 경우를 대비해
    명시적으로 생성 보장한다.
    """
    try:
        with engine.connect() as conn:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_collection_metrics_tenant_time "
                "ON collection_metrics (tenant_id, collected_at)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_collection_metrics_type_time "
                "ON collection_metrics (tenant_id, data_type, collected_at)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_collection_metrics_fallback "
                "ON collection_metrics (tenant_id, fallback_used, collected_at)"
            ))
            conn.commit()
    except Exception as e:
        logger.warning(f"collection_metrics 인덱스 보장 실패: {e}")


def _migrate_send_history_newsletter_type(engine) -> None:
    """기존 send_history 테이블에 newsletter_type 컬럼 추가 (마이그레이션)"""
    try:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(send_history)"))
            columns = [row[1] for row in result]
            if "newsletter_type" not in columns:
                conn.execute(text(
                    "ALTER TABLE send_history ADD COLUMN newsletter_type VARCHAR(20) DEFAULT 'daily' NOT NULL"
                ))
                conn.commit()
                logger.info("send_history 테이블에 newsletter_type 컬럼 추가 완료")
    except Exception as e:
        logger.warning(f"send_history 마이그레이션 실패 — 스키마가 불완전할 수 있음: {e}")


def _migrate_send_history_send_mode(engine) -> None:
    """send_history 테이블에 send_mode 컬럼 추가 (주말 관리자 테스트 모드 분리용).

    'normal' = 정식 발송, 'weekend_test' = 주말 관리자 테스트.
    통계/대시보드는 기본적으로 'normal'만 집계해야 한다.
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(text("PRAGMA table_info(send_history)"))
            columns = [row[1] for row in result]
            if "send_mode" not in columns:
                conn.execute(text(
                    "ALTER TABLE send_history ADD COLUMN send_mode VARCHAR(20) DEFAULT 'normal' NOT NULL"
                ))
                conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_send_history_mode "
                    "ON send_history (tenant_id, send_mode, sent_at)"
                ))
                conn.commit()
                logger.info("send_history 테이블에 send_mode 컬럼 + 인덱스 추가 완료")
    except Exception as e:
        logger.warning(f"send_history send_mode 마이그레이션 실패 — 스키마가 불완전할 수 있음: {e}")


def run_all_migrations(engine) -> None:
    """init_db 에서 1회 호출 — 등록된 마이그레이션 전부 실행."""
    _migrate_send_history_newsletter_type(engine)
    _migrate_send_history_send_mode(engine)
    _migrate_subscriber_send_slot(engine)
    _migrate_sent_articles_company_name(engine)
    _migrate_collection_metrics(engine)
    _migrate_subscriber_persona_columns(engine)
    _migrate_email_verification_signup_meta(engine)
