"""
데이터베이스 저장소 패턴 구현
"""

import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

from sqlalchemy import create_engine, and_, func, or_, text, Integer
from sqlalchemy.orm import sessionmaker, Session

logger = logging.getLogger(__name__)

from .models import (
    Base, Subscriber, SendHistory, CollectedData,
    CollectedDataHistory, EmailVerification, VerificationType,
    NewsletterType
)


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
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

    Base.metadata.create_all(bind=_engine)

    _migrate_send_history_newsletter_type(_engine)


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
        logger.debug(f"send_history 마이그레이션 스킵: {e}")


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


class SubscriberRepository:
    """구독자 저장소"""

    @staticmethod
    def create(session: Session, tenant_id: str, email: str, name: str,
               unsubscribe_token: str) -> Subscriber:
        subscriber = Subscriber(
            tenant_id=tenant_id,
            email=email,
            name=name,
            unsubscribe_token=unsubscribe_token
        )
        session.add(subscriber)
        session.flush()
        return subscriber

    @staticmethod
    def get_by_email(session: Session, tenant_id: str, email: str) -> Optional[Subscriber]:
        return session.query(Subscriber).filter(
            and_(Subscriber.tenant_id == tenant_id, Subscriber.email == email)
        ).first()

    @staticmethod
    def get_active_by_email(session: Session, tenant_id: str, email: str) -> Optional[Subscriber]:
        return session.query(Subscriber).filter(
            and_(
                Subscriber.tenant_id == tenant_id,
                Subscriber.email == email,
                Subscriber.is_active == True
            )
        ).first()

    @staticmethod
    def get_all_active(session: Session, tenant_id: str) -> list[Subscriber]:
        return session.query(Subscriber).filter(
            and_(Subscriber.tenant_id == tenant_id, Subscriber.is_active == True)
        ).all()

    @staticmethod
    def get_by_unsubscribe_token(session: Session, token: str) -> Optional[Subscriber]:
        return session.query(Subscriber).filter(
            and_(Subscriber.unsubscribe_token == token, Subscriber.is_active == True)
        ).first()

    @staticmethod
    def count_by_tenant(session: Session, tenant_id: str, active_only: bool = True) -> int:
        """테넌트별 구독자 수"""
        query = session.query(func.count(Subscriber.id)).filter(
            Subscriber.tenant_id == tenant_id
        )
        if active_only:
            query = query.filter(Subscriber.is_active == True)
        return query.scalar() or 0

    @staticmethod
    def get_by_id(session: Session, subscriber_id: int) -> Optional[Subscriber]:
        """ID로 구독자 조회"""
        return session.query(Subscriber).filter(Subscriber.id == subscriber_id).first()

    @staticmethod
    def get_all_by_tenant(
        session: Session,
        tenant_id: str,
        active_only: Optional[bool] = None,
        search: str = "",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Subscriber], int]:
        """테넌트별 구독자 목록 (페이지네이션, 검색)"""
        query = session.query(Subscriber).filter(Subscriber.tenant_id == tenant_id)
        if active_only is True:
            query = query.filter(Subscriber.is_active == True)
        elif active_only is False:
            query = query.filter(Subscriber.is_active == False)
        if search:
            pattern = f"%{search}%"
            query = query.filter(
                or_(Subscriber.email.ilike(pattern), Subscriber.name.ilike(pattern))
            )
        total = query.count()
        items = query.order_by(Subscriber.created_at.desc()).offset(offset).limit(limit).all()
        return items, total


class SendHistoryRepository:
    """발송 이력 저장소"""

    @staticmethod
    def create(session: Session, tenant_id: str, subscriber_id: int,
               subject: str, is_success: bool, error_message: str = None,
               newsletter_type: str = "daily") -> SendHistory:
        history = SendHistory(
            tenant_id=tenant_id,
            subscriber_id=subscriber_id,
            subject=subject,
            newsletter_type=newsletter_type,
            is_success=is_success,
            error_message=error_message
        )
        session.add(history)
        session.flush()
        return history

    @staticmethod
    def already_sent_today(session: Session, tenant_id: str, subscriber_id: int) -> bool:
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        return (
            session.query(SendHistory)
            .filter(
                and_(
                    SendHistory.tenant_id == tenant_id,
                    SendHistory.subscriber_id == subscriber_id,
                    SendHistory.sent_at >= today_start,
                    SendHistory.is_success == True
                )
            )
            .count() > 0
        )

    @staticmethod
    def get_sent_today_subscriber_ids(session: Session, tenant_id: str) -> set[int]:
        """당일 발송 완료된 구독자 ID 일괄 조회 (N+1 방지)"""
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        rows = (
            session.query(SendHistory.subscriber_id)
            .filter(
                and_(
                    SendHistory.tenant_id == tenant_id,
                    SendHistory.sent_at >= today_start,
                    SendHistory.is_success == True
                )
            )
            .distinct()
            .all()
        )
        return {row[0] for row in rows}

    @staticmethod
    def get_today_stats(session: Session, tenant_id: str) -> dict:
        """오늘 발송 통계: {total, success, failed}"""
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        rows = (
            session.query(
                SendHistory.is_success,
                func.count(SendHistory.id),
            )
            .filter(
                and_(
                    SendHistory.tenant_id == tenant_id,
                    SendHistory.sent_at >= today_start,
                )
            )
            .group_by(SendHistory.is_success)
            .all()
        )
        stats = {"total": 0, "success": 0, "failed": 0}
        for is_success, cnt in rows:
            if is_success:
                stats["success"] = cnt
            else:
                stats["failed"] = cnt
            stats["total"] += cnt
        return stats

    @staticmethod
    def get_recent_errors(session: Session, tenant_id: str, limit: int = 10) -> list[SendHistory]:
        """최근 발송 실패 이력"""
        return (
            session.query(SendHistory)
            .filter(
                and_(
                    SendHistory.tenant_id == tenant_id,
                    SendHistory.is_success == False,
                )
            )
            .order_by(SendHistory.sent_at.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_history_paginated(
        session: Session,
        tenant_id: str,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        success_only: Optional[bool] = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[SendHistory], int]:
        """발송 이력 페이지네이션"""
        query = session.query(SendHistory).filter(SendHistory.tenant_id == tenant_id)
        if date_from:
            query = query.filter(SendHistory.sent_at >= date_from)
        if date_to:
            query = query.filter(SendHistory.sent_at < date_to + timedelta(days=1))
        if success_only is True:
            query = query.filter(SendHistory.is_success == True)
        elif success_only is False:
            query = query.filter(SendHistory.is_success == False)
        total = query.count()
        items = query.order_by(SendHistory.sent_at.desc()).offset(offset).limit(limit).all()
        return items, total

    @staticmethod
    def get_daily_summary(session: Session, tenant_id: str, days: int = 7) -> list[dict]:
        """최근 N일 일별 발송 요약"""
        since = datetime.utcnow() - timedelta(days=days)
        rows = (
            session.query(
                func.date(SendHistory.sent_at).label("date"),
                func.count(SendHistory.id).label("total"),
                func.sum(func.cast(SendHistory.is_success, Integer)).label("success"),
            )
            .filter(
                and_(
                    SendHistory.tenant_id == tenant_id,
                    SendHistory.sent_at >= since,
                )
            )
            .group_by(func.date(SendHistory.sent_at))
            .order_by(func.date(SendHistory.sent_at).desc())
            .all()
        )
        return [
            {"date": str(row.date), "total": row.total, "success": row.success or 0,
             "failed": row.total - (row.success or 0)}
            for row in rows
        ]

    @staticmethod
    def get_sent_subscriber_ids_for_period(
        session: Session, tenant_id: str,
        newsletter_type: str, period_start: datetime
    ) -> set[int]:
        """주기별 발송 완료된 구독자 ID 조회 (weekly/monthly 중복 방지)"""
        rows = (
            session.query(SendHistory.subscriber_id)
            .filter(
                and_(
                    SendHistory.tenant_id == tenant_id,
                    SendHistory.newsletter_type == newsletter_type,
                    SendHistory.sent_at >= period_start,
                    SendHistory.is_success == True
                )
            )
            .distinct()
            .all()
        )
        return {row[0] for row in rows}


class CollectedDataRepository:
    """수집 데이터 저장소"""

    @staticmethod
    def upsert(session: Session, tenant_id: str, data_type: str, data: dict) -> CollectedData:
        """데이터 저장 (기존 데이터 덮어쓰기)"""
        existing = session.query(CollectedData).filter(
            and_(
                CollectedData.tenant_id == tenant_id,
                CollectedData.data_type == data_type
            )
        ).first()

        data_json = json.dumps(data, ensure_ascii=False, default=str)

        if existing:
            existing.data_json = data_json
            existing.collected_at = datetime.utcnow()
            session.flush()
            return existing

        record = CollectedData(
            tenant_id=tenant_id,
            data_type=data_type,
            data_json=data_json
        )
        session.add(record)
        session.flush()
        return record

    @staticmethod
    def get_latest(session: Session, tenant_id: str, data_type: str) -> Optional[dict]:
        """최신 수집 데이터 조회"""
        record = session.query(CollectedData).filter(
            and_(
                CollectedData.tenant_id == tenant_id,
                CollectedData.data_type == data_type
            )
        ).order_by(CollectedData.collected_at.desc()).first()

        if record:
            return json.loads(record.data_json)
        return None

    @staticmethod
    def get_all_latest(session: Session, tenant_id: str) -> dict:
        """테넌트의 모든 최신 수집 데이터 조회"""
        from sqlalchemy import func

        subquery = (
            session.query(
                CollectedData.data_type,
                func.max(CollectedData.id).label("max_id")
            )
            .filter(CollectedData.tenant_id == tenant_id)
            .group_by(CollectedData.data_type)
            .subquery()
        )

        records = (
            session.query(CollectedData)
            .join(subquery, CollectedData.id == subquery.c.max_id)
            .all()
        )

        result = {}
        for record in records:
            result[record.data_type] = json.loads(record.data_json)
        return result


    @staticmethod
    def get_all_latest_with_time(session: Session, tenant_id: str) -> dict:
        """테넌트의 모든 최신 수집 데이터와 수집 시각 함께 반환

        Returns:
            {data_type: (data_dict, collected_at)}
        """
        from sqlalchemy import func

        subquery = (
            session.query(
                CollectedData.data_type,
                func.max(CollectedData.id).label("max_id")
            )
            .filter(CollectedData.tenant_id == tenant_id)
            .group_by(CollectedData.data_type)
            .subquery()
        )

        records = (
            session.query(CollectedData)
            .join(subquery, CollectedData.id == subquery.c.max_id)
            .all()
        )

        result = {}
        for record in records:
            result[record.data_type] = (
                json.loads(record.data_json),
                record.collected_at,
            )
        return result

    @staticmethod
    def save_to_history(session: Session, tenant_id: str, data_type: str,
                        data: dict, collected_date: date = None) -> CollectedDataHistory:
        """일일 수집 데이터를 이력 테이블에 저장 (upsert)"""
        if collected_date is None:
            collected_date = date.today()

        data_json = json.dumps(data, ensure_ascii=False, default=str)

        existing = session.query(CollectedDataHistory).filter(
            and_(
                CollectedDataHistory.tenant_id == tenant_id,
                CollectedDataHistory.data_type == data_type,
                CollectedDataHistory.collected_date == collected_date,
            )
        ).first()

        if existing:
            existing.data_json = data_json
            existing.collected_at = datetime.utcnow()
            session.flush()
            return existing

        record = CollectedDataHistory(
            tenant_id=tenant_id,
            data_type=data_type,
            data_json=data_json,
            collected_date=collected_date,
        )
        session.add(record)
        session.flush()
        return record

    @staticmethod
    def get_history_range(session: Session, tenant_id: str,
                          date_from: date, date_to: date) -> list[dict]:
        """기간별 이력 조회 - 날짜별 수집 데이터 리스트 반환

        Returns:
            [{collected_date, data_type, data}, ...]
        """
        records = (
            session.query(CollectedDataHistory)
            .filter(
                and_(
                    CollectedDataHistory.tenant_id == tenant_id,
                    CollectedDataHistory.collected_date >= date_from,
                    CollectedDataHistory.collected_date <= date_to,
                )
            )
            .order_by(CollectedDataHistory.collected_date.asc())
            .all()
        )
        return [
            {
                "collected_date": record.collected_date,
                "data_type": record.data_type,
                "data": json.loads(record.data_json),
            }
            for record in records
        ]


class EmailVerificationRepository:
    """이메일 인증 저장소"""

    @staticmethod
    def create(session: Session, tenant_id: str, email: str, name: str,
               code: str, verification_type: VerificationType,
               expires_at: datetime) -> EmailVerification:
        verification = EmailVerification(
            tenant_id=tenant_id,
            email=email,
            name=name,
            code=code,
            verification_type=verification_type,
            expires_at=expires_at
        )
        session.add(verification)
        session.flush()
        return verification

    @staticmethod
    def get_by_id_and_email(session: Session, verification_id: int,
                            email: str) -> Optional[EmailVerification]:
        return session.query(EmailVerification).filter(
            and_(
                EmailVerification.id == verification_id,
                EmailVerification.email == email
            )
        ).first()

    @staticmethod
    def get_unsubscribe_by_id_and_email(session: Session, verification_id: int,
                                         email: str) -> Optional[EmailVerification]:
        return session.query(EmailVerification).filter(
            and_(
                EmailVerification.id == verification_id,
                EmailVerification.email == email,
                EmailVerification.verification_type == VerificationType.UNSUBSCRIBE
            )
        ).first()

    @staticmethod
    def delete_pending(session: Session, tenant_id: str, email: str,
                       verification_type: VerificationType = None) -> None:
        query = session.query(EmailVerification).filter(
            and_(
                EmailVerification.tenant_id == tenant_id,
                EmailVerification.email == email,
                EmailVerification.is_verified == False
            )
        )
        if verification_type:
            query = query.filter(EmailVerification.verification_type == verification_type)
        query.delete()
