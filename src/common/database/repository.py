"""
데이터베이스 저장소 패턴 구현
"""

import json
import logging
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

from sqlalchemy import create_engine, and_, func, or_, text, Integer
from sqlalchemy.orm import sessionmaker, Session

logger = logging.getLogger(__name__)

# KST (UTC+9)
_KST = timezone(timedelta(hours=9))


def _today_start_utc() -> datetime:
    """KST 기준 오늘 자정을 naive UTC datetime으로 반환

    컨테이너 TZ=Asia/Seoul 환경에서 datetime.utcnow()를 사용하면
    UTC 자정(=KST 09:00)이 경계가 되어 전일 09:00~당일 08:59 KST가
    같은 "오늘"로 묶이는 문제가 있다.
    KST 자정을 UTC로 환산(전일 15:00 UTC)하여 올바른 경계를 사용한다.
    """
    now_kst = datetime.now(_KST)
    midnight_kst = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
    midnight_utc = midnight_kst.astimezone(timezone.utc)
    return midnight_utc.replace(tzinfo=None)

from .models import (
    Base, Subscriber, SendHistory, CollectedData,
    CollectedDataHistory, EmailVerification, VerificationType,
    NewsletterType, NewsletterArchive, SentArticle, BounceLog,
    CollectionMetric, SubscriberTopicRequest,
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
    _migrate_send_history_send_mode(_engine)
    _migrate_subscriber_send_slot(_engine)
    _migrate_sent_articles_company_name(_engine)
    _migrate_collection_metrics(_engine)
    _migrate_subscriber_persona_columns(_engine)
    _migrate_email_verification_signup_meta(_engine)


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
        logger.debug(f"subscribers send_slot 마이그레이션 스킵: {e}")


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
        logger.debug(f"subscribers 페르소나 마이그레이션 스킵: {e}")


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
        logger.debug(f"email_verifications signup_meta 마이그레이션 스킵: {e}")


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
        logger.debug(f"sent_articles company_name 마이그레이션 스킵: {e}")


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
        logger.debug(f"collection_metrics 인덱스 보장 스킵: {e}")


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
        logger.debug(f"send_history send_mode 마이그레이션 스킵: {e}")


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
               unsubscribe_token: str,
               persona_code: Optional[str] = None,
               purpose: Optional[str] = None,
               depth_level: str = "practical",
               interests: Optional[list] = None) -> Subscriber:
        """구독자 생성. 페르소나 인자는 선택 — 미지정 시 기존과 동일 동작."""
        subscriber = Subscriber(
            tenant_id=tenant_id,
            email=email,
            name=name,
            unsubscribe_token=unsubscribe_token,
            persona_code=persona_code or None,
            purpose=purpose or None,
            depth_level=depth_level or "practical",
            interests=(json.dumps(interests, ensure_ascii=False)
                       if interests else None),
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
    def get_active_by_slot(session: Session, tenant_id: str, slot: str) -> list[Subscriber]:
        """특정 슬롯에 속한 활성 구독자 (NULL 슬롯은 DEFAULT_SLOT으로 간주)"""
        from ..scheduler.slots import DEFAULT_SLOT
        if slot == DEFAULT_SLOT:
            slot_filter = or_(Subscriber.send_slot == slot, Subscriber.send_slot.is_(None))
        else:
            slot_filter = Subscriber.send_slot == slot
        return session.query(Subscriber).filter(
            and_(
                Subscriber.tenant_id == tenant_id,
                Subscriber.is_active == True,
                slot_filter,
            )
        ).all()

    @staticmethod
    def count_by_slot(session: Session, tenant_id: str) -> dict:
        """슬롯별 활성 구독자 수: {'early': N, 'mid': N, 'late': N}"""
        from ..scheduler.slots import DEFAULT_SLOT, SLOT_KEYS
        rows = (
            session.query(Subscriber.send_slot, func.count(Subscriber.id))
            .filter(
                and_(
                    Subscriber.tenant_id == tenant_id,
                    Subscriber.is_active == True,
                )
            )
            .group_by(Subscriber.send_slot)
            .all()
        )
        result = {key: 0 for key in SLOT_KEYS}
        for slot, cnt in rows:
            key = slot if slot in SLOT_KEYS else DEFAULT_SLOT
            result[key] = result.get(key, 0) + cnt
        return result

    @staticmethod
    def bulk_update_slot(session: Session, tenant_id: str,
                         subscriber_ids: list[int], new_slot: str) -> int:
        """선택한 구독자들의 슬롯을 일괄 변경. 변경된 행 수 반환"""
        if not subscriber_ids:
            return 0
        updated = (
            session.query(Subscriber)
            .filter(
                and_(
                    Subscriber.tenant_id == tenant_id,
                    Subscriber.id.in_(subscriber_ids),
                )
            )
            .update({Subscriber.send_slot: new_slot}, synchronize_session=False)
        )
        session.flush()
        return updated

    @staticmethod
    def update_slot(session: Session, subscriber_id: int, new_slot: str) -> bool:
        subscriber = session.query(Subscriber).filter(Subscriber.id == subscriber_id).first()
        if not subscriber:
            return False
        subscriber.send_slot = new_slot
        session.flush()
        return True

    @staticmethod
    def delete(session: Session, subscriber_id: int) -> bool:
        """구독자 영구 삭제. 삭제되면 True, 없으면 False"""
        subscriber = session.query(Subscriber).filter(Subscriber.id == subscriber_id).first()
        if not subscriber:
            return False
        session.delete(subscriber)
        session.flush()
        return True

    @staticmethod
    def get_by_unsubscribe_token(session: Session, token: str) -> Optional[Subscriber]:
        return session.query(Subscriber).filter(
            and_(Subscriber.unsubscribe_token == token, Subscriber.is_active == True)
        ).first()

    @staticmethod
    def deactivate_all_by_email(session: Session, email: str) -> int:
        """이메일이 hard bounce된 경우 전 테넌트의 동일 이메일 구독자 비활성화. 변경 행 수 반환"""
        updated = (
            session.query(Subscriber)
            .filter(and_(Subscriber.email == email, Subscriber.is_active == True))
            .update(
                {Subscriber.is_active: False, Subscriber.updated_at: datetime.utcnow()},
                synchronize_session=False,
            )
        )
        session.flush()
        return updated

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

    # --- 페르소나 적응형 뉴스레터 (N1) ---

    @staticmethod
    def update_persona(session: Session, subscriber_id: int, *,
                       persona_code: Optional[str] = None,
                       purpose: Optional[str] = None,
                       depth_level: Optional[str] = None,
                       interests: Optional[list] = None) -> bool:
        """구독 관리 페이지에서 페르소나 설정 변경.

        None 인자는 '변경 안 함'. 빈 문자열/빈 리스트는 'NULL 로 비움'.
        """
        subscriber = session.query(Subscriber).filter(
            Subscriber.id == subscriber_id
        ).first()
        if not subscriber:
            return False
        if persona_code is not None:
            subscriber.persona_code = persona_code or None
        if purpose is not None:
            subscriber.purpose = purpose or None
        if depth_level is not None:
            subscriber.depth_level = depth_level or "practical"
        if interests is not None:
            subscriber.interests = (
                json.dumps(interests, ensure_ascii=False) if interests else None
            )
        session.flush()
        return True

    @staticmethod
    def get_active_personas(session: Session, tenant_id: str) -> list[str]:
        """활성 구독자에 존재하는 distinct persona_code. NULL 은 'patient' 로 합산.

        N3 페르소나 세그먼트 순회용 선반영.
        """
        rows = (
            session.query(Subscriber.persona_code)
            .filter(and_(
                Subscriber.tenant_id == tenant_id,
                Subscriber.is_active == True,
            ))
            .distinct()
            .all()
        )
        codes = {(code or "patient") for (code,) in rows}
        return sorted(codes)

    @staticmethod
    def get_active_by_persona(session: Session, tenant_id: str,
                              persona_code: str) -> list[Subscriber]:
        """persona_code 세그먼트의 활성 구독자.

        persona_code 가 'patient' 면 persona_code IS NULL 인 행도 합류 (N3 선반영).
        """
        if persona_code == "patient":
            persona_filter = or_(
                Subscriber.persona_code == "patient",
                Subscriber.persona_code.is_(None),
            )
        else:
            persona_filter = Subscriber.persona_code == persona_code
        return session.query(Subscriber).filter(
            and_(
                Subscriber.tenant_id == tenant_id,
                Subscriber.is_active == True,
                persona_filter,
            )
        ).all()


class SubscriberTopicRequestRepository:
    """콘텐츠 선택·변형 요청 미러 저장소 (UI 표시·재요청용).

    정본 로그는 AllergyInsight `newsletter_topic_requests`.
    """

    @staticmethod
    def create(session: Session, *, tenant_id: str, subscriber_id: int,
               request_id: str, request_type: str,
               topic: Optional[str] = None) -> SubscriberTopicRequest:
        row = SubscriberTopicRequest(
            tenant_id=tenant_id,
            subscriber_id=subscriber_id,
            request_id=request_id,
            request_type=request_type,
            topic=topic,
            coverage="pending",
        )
        session.add(row)
        session.flush()
        return row

    @staticmethod
    def get_by_request_id(session: Session,
                          request_id: str) -> Optional[SubscriberTopicRequest]:
        """멱등성 — 동일 request_id 재요청 시 기존 행 반환."""
        return session.query(SubscriberTopicRequest).filter(
            SubscriberTopicRequest.request_id == request_id
        ).first()

    @staticmethod
    def get_by_job_id(session: Session,
                      job_id: str) -> Optional[SubscriberTopicRequest]:
        """콜백 수신 시 job_id 로 미러 행 조회."""
        return session.query(SubscriberTopicRequest).filter(
            SubscriberTopicRequest.job_id == job_id
        ).first()

    @staticmethod
    def update_result(session: Session, request_id: str, *, coverage: str,
                      job_id: Optional[str] = None,
                      result_json: Optional[str] = None) -> bool:
        """진단 응답·콜백 결과 반영. 콜백 중복 수신 dedup 은 호출부 책임."""
        row = session.query(SubscriberTopicRequest).filter(
            SubscriberTopicRequest.request_id == request_id
        ).first()
        if not row:
            return False
        row.coverage = coverage
        if job_id is not None:
            row.job_id = job_id
        if result_json is not None:
            row.result_json = result_json
        session.flush()
        return True

    @staticmethod
    def list_by_subscriber(session: Session, tenant_id: str, subscriber_id: int,
                           limit: int = 20) -> list[SubscriberTopicRequest]:
        """구독 관리 페이지 요청 이력 표시용."""
        return (
            session.query(SubscriberTopicRequest)
            .filter(and_(
                SubscriberTopicRequest.tenant_id == tenant_id,
                SubscriberTopicRequest.subscriber_id == subscriber_id,
            ))
            .order_by(SubscriberTopicRequest.created_at.desc())
            .limit(limit)
            .all()
        )


class SendHistoryRepository:
    """발송 이력 저장소"""

    @staticmethod
    def create(session: Session, tenant_id: str, subscriber_id: int,
               subject: str, is_success: bool, error_message: str = None,
               newsletter_type: str = "daily",
               send_mode: str = "normal") -> SendHistory:
        history = SendHistory(
            tenant_id=tenant_id,
            subscriber_id=subscriber_id,
            subject=subject,
            newsletter_type=newsletter_type,
            send_mode=send_mode,
            is_success=is_success,
            error_message=error_message
        )
        session.add(history)
        session.flush()
        return history

    @staticmethod
    def already_sent_today(session: Session, tenant_id: str, subscriber_id: int,
                           newsletter_type: str = "daily") -> bool:
        today_start = _today_start_utc()
        return (
            session.query(SendHistory)
            .filter(
                and_(
                    SendHistory.tenant_id == tenant_id,
                    SendHistory.subscriber_id == subscriber_id,
                    SendHistory.newsletter_type == newsletter_type,
                    SendHistory.sent_at >= today_start,
                    SendHistory.is_success == True
                )
            )
            .count() > 0
        )

    @staticmethod
    def get_sent_today_subscriber_ids(session: Session, tenant_id: str,
                                      newsletter_type: str = "daily") -> set[int]:
        """당일 발송 완료된 구독자 ID 일괄 조회 (N+1 방지, newsletter_type별 분리)"""
        today_start = _today_start_utc()
        rows = (
            session.query(SendHistory.subscriber_id)
            .filter(
                and_(
                    SendHistory.tenant_id == tenant_id,
                    SendHistory.newsletter_type == newsletter_type,
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
        today_start = _today_start_utc()
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
    def get_history_all_paginated(
        session: Session,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        success_only: Optional[bool] = None,
        tenant_filter: Optional[str] = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[SendHistory], int]:
        """전체 테넌트 발송 이력 페이지네이션"""
        query = session.query(SendHistory)
        if tenant_filter:
            query = query.filter(SendHistory.tenant_id == tenant_filter)
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
    def get_daily_summary_all(session: Session, days: int = 7) -> list[dict]:
        """전체 테넌트 최근 N일 일별 발송 요약"""
        since = datetime.utcnow() - timedelta(days=days)
        rows = (
            session.query(
                func.date(SendHistory.sent_at).label("date"),
                func.count(SendHistory.id).label("total"),
                func.sum(func.cast(SendHistory.is_success, Integer)).label("success"),
            )
            .filter(SendHistory.sent_at >= since)
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


class NewsletterArchiveRepository:
    """뉴스레터 아카이브 저장소"""

    @staticmethod
    def save(session: Session, tenant_id: str, newsletter_type: str,
             subject: str, html_content: str, sent_date: date = None) -> NewsletterArchive:
        """아카이브 저장 (같은 tenant/type/date는 덮어쓰기)"""
        if sent_date is None:
            sent_date = date.today()

        existing = session.query(NewsletterArchive).filter(
            and_(
                NewsletterArchive.tenant_id == tenant_id,
                NewsletterArchive.newsletter_type == newsletter_type,
                NewsletterArchive.sent_date == sent_date,
            )
        ).first()

        if existing:
            existing.subject = subject
            existing.html_content = html_content
            existing.created_at = datetime.utcnow()
            session.flush()
            return existing

        archive = NewsletterArchive(
            tenant_id=tenant_id,
            newsletter_type=newsletter_type,
            subject=subject,
            html_content=html_content,
            sent_date=sent_date,
        )
        session.add(archive)
        session.flush()
        return archive

    @staticmethod
    def get_list(session: Session, tenant_id: str,
                 limit: int = 50) -> list[NewsletterArchive]:
        """아카이브 목록 조회 (최신순)"""
        return (
            session.query(NewsletterArchive)
            .filter(NewsletterArchive.tenant_id == tenant_id)
            .order_by(NewsletterArchive.sent_date.desc(), NewsletterArchive.newsletter_type.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_all_list(session: Session, limit: int = 100) -> list[NewsletterArchive]:
        """전체 테넌트 아카이브 목록 조회 (최신순)"""
        return (
            session.query(NewsletterArchive)
            .order_by(NewsletterArchive.sent_date.desc(), NewsletterArchive.newsletter_type.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_by_id(session: Session, archive_id: int) -> Optional[NewsletterArchive]:
        """ID로 아카이브 조회"""
        return session.query(NewsletterArchive).filter(
            NewsletterArchive.id == archive_id
        ).first()

    @staticmethod
    def get_latest_before(
        session: Session,
        tenant_id: str,
        newsletter_type: str,
        before_date: date,
    ) -> Optional[NewsletterArchive]:
        """before_date 이전 가장 최근 archive. duplicate-content 가드(AC-9)용."""
        return (
            session.query(NewsletterArchive)
            .filter(
                NewsletterArchive.tenant_id == tenant_id,
                NewsletterArchive.newsletter_type == newsletter_type,
                NewsletterArchive.sent_date < before_date,
            )
            .order_by(NewsletterArchive.sent_date.desc())
            .first()
        )


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

    @staticmethod
    def count_recent_by_email(session: Session, email: str,
                              since: datetime) -> int:
        """주어진 시각 이후 동일 이메일로 발급된 인증 요청 수 (어뷰즈 rate limit 용)"""
        return session.query(EmailVerification).filter(
            and_(
                EmailVerification.email == email,
                EmailVerification.created_at >= since,
            )
        ).count()


class BounceLogRepository:
    """NDR(bounce) 이력 저장소"""

    @staticmethod
    def create(session: Session, email: str, bounce_type: str,
               smtp_code: Optional[str], diagnostic: Optional[str],
               ndr_message_id: Optional[str]) -> Optional[BounceLog]:
        """bounce 기록. ndr_message_id 중복이면 None 반환 (재처리 방지)"""
        if ndr_message_id:
            existing = session.query(BounceLog).filter(
                BounceLog.ndr_message_id == ndr_message_id
            ).first()
            if existing:
                return None
        entry = BounceLog(
            email=email.strip().lower(),
            bounce_type=bounce_type,
            smtp_code=smtp_code,
            diagnostic=(diagnostic or "")[:2000],  # 본문 발췌 길이 제한
            ndr_message_id=ndr_message_id,
        )
        session.add(entry)
        session.flush()
        return entry

    @staticmethod
    def has_recent_hard_bounce(session: Session, email: str, days: int = 30) -> bool:
        """최근 N일 내 hard bounce 이력 존재 여부 (request_subscribe 사전 차단용)"""
        since = datetime.utcnow() - timedelta(days=days)
        return session.query(BounceLog).filter(
            and_(
                BounceLog.email == email.strip().lower(),
                BounceLog.bounce_type == "hard",
                BounceLog.created_at >= since,
            )
        ).first() is not None

    @staticmethod
    def get_recent(session: Session, days: int = 7, limit: int = 200) -> list[BounceLog]:
        """최근 N일 bounce 목록 (admin 가시화용)"""
        since = datetime.utcnow() - timedelta(days=days)
        return (
            session.query(BounceLog)
            .filter(BounceLog.created_at >= since)
            .order_by(BounceLog.created_at.desc())
            .limit(limit)
            .all()
        )


class SentArticleRepository:
    """발송 기사 이력 저장소 (교차일 dedup 용)

    동일 테넌트에서 최근 N일 내 이미 발송된 기사 ID 를 조회하여
    수집/선정 단계에서 제외(exclude_ids)하기 위한 저장소.
    `UNIQUE(tenant_id, article_id, section, sent_date)` 로 멱등 보장.
    """

    @staticmethod
    def list_recent_article_ids(session: Session, tenant_id: str,
                                days: int = 7) -> list[int]:
        """최근 N일(KST 기준) 내 해당 테넌트에서 발송된 article_id 목록.

        Returns:
            중복 제거된 article_id 리스트 (sent_at DESC 순).
        """
        cutoff = _today_start_utc() - timedelta(days=days)
        rows = (
            session.query(SentArticle.article_id)
            .filter(
                and_(
                    SentArticle.tenant_id == tenant_id,
                    SentArticle.sent_at >= cutoff,
                )
            )
            .order_by(SentArticle.sent_at.desc())
            .distinct()
            .all()
        )
        return [row[0] for row in rows]

    @staticmethod
    def list_recent_company_names(session: Session, tenant_id: str,
                                   days: int = 7) -> list[str]:
        """최근 N일 내 해당 테넌트에서 발송된 기업명 목록.

        company-digest 일 단위 반복 노출 차단용. 헤드라인/디그스트 양쪽에
        남긴 company_name 을 모두 모으되 None/빈문자열은 제외한다.
        """
        cutoff = _today_start_utc() - timedelta(days=days)
        rows = (
            session.query(SentArticle.company_name)
            .filter(
                and_(
                    SentArticle.tenant_id == tenant_id,
                    SentArticle.sent_at >= cutoff,
                    SentArticle.company_name.isnot(None),
                    SentArticle.company_name != "",
                )
            )
            .order_by(SentArticle.sent_at.desc())
            .distinct()
            .all()
        )
        return [row[0] for row in rows]

    @staticmethod
    def record_sent_articles(
        session: Session, tenant_id: str,
        sent_date: date,
        entries: list[tuple],
    ) -> int:
        """발송된 기사 이력을 기록 (멱등: 중복 키는 무시).

        Args:
            entries: 4-튜플 `(article_id, article_url, section, company_name)` 권장.
                3-튜플 `(article_id, article_url, section)` 도 호환(기업명 None).

        Returns:
            실제로 신규 INSERT 된 건수.
        """
        if not entries:
            return 0

        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        payload = []
        for entry in entries:
            if len(entry) == 4:
                aid, url, section, company = entry
            elif len(entry) == 3:
                aid, url, section = entry
                company = None
            else:
                continue
            if aid is None or not section:
                continue
            payload.append({
                "tenant_id": tenant_id,
                "article_id": aid,
                "article_url": url,
                "section": section,
                "sent_date": sent_date,
                "company_name": (company or None),
            })
        if not payload:
            return 0
        stmt = sqlite_insert(SentArticle).values(payload)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["tenant_id", "article_id", "section", "sent_date"]
        )
        result = session.execute(stmt)
        return result.rowcount or 0

    @staticmethod
    def purge_older_than(session: Session, days: int = 90) -> int:
        """보존 기간(기본 90일) 초과 이력 삭제. 주간 cron 용.

        Returns:
            삭제된 행 수.
        """
        cutoff = _today_start_utc() - timedelta(days=days)
        deleted = (
            session.query(SentArticle)
            .filter(SentArticle.sent_at < cutoff)
            .delete(synchronize_session=False)
        )
        return int(deleted or 0)


class CollectionMetricRepository:
    """수집 메트릭 저장소

    collector 가 누적해 둔 _metrics 딕셔너리 리스트를 한 번에 적재한다.
    소비처:
      - 운영 가시화 (admin dashboard)
      - 회귀 감지 (P2 diff 배너)
      - 트랙 E V1 digest 노출 메트릭 집계
    """

    _ALLOWED_KEYS = {
        "data_type", "api_path",
        "raw_count", "final_count",
        "excluded_by_ids", "excluded_by_companies",
        "effective_days", "fallback_used", "latency_ms", "error",
    }

    @staticmethod
    def record_many(
        session: Session,
        tenant_id: str,
        newsletter_type: str,
        metrics: list[dict],
    ) -> int:
        """수집 메트릭 다건 적재. 실제 INSERT 된 건수 반환.

        - `metrics` 각 항목은 collector 의 `_track()` 컨텍스트 매니저가 채운 dict
        - 필수 키: `data_type`. 나머지는 default 적용.
        - 빈 리스트면 0 반환 (no-op).
        """
        if not metrics:
            return 0
        rows: list[CollectionMetric] = []
        now = datetime.utcnow()
        for m in metrics:
            data_type = m.get("data_type")
            if not data_type:
                continue
            row = CollectionMetric(
                tenant_id=tenant_id,
                newsletter_type=newsletter_type or "daily",
                data_type=str(data_type)[:50],
                api_path=(m.get("api_path") or None),
                raw_count=int(m.get("raw_count") or 0),
                final_count=int(m.get("final_count") or 0),
                excluded_by_ids=int(m.get("excluded_by_ids") or 0),
                excluded_by_companies=int(m.get("excluded_by_companies") or 0),
                effective_days=(
                    int(m["effective_days"])
                    if m.get("effective_days") is not None else None
                ),
                fallback_used=bool(m.get("fallback_used") or False),
                latency_ms=int(m.get("latency_ms") or 0),
                error=((m.get("error") or None) and str(m.get("error"))[:500]),
                collected_at=now,
            )
            rows.append(row)
        if not rows:
            return 0
        session.add_all(rows)
        session.flush()
        return len(rows)

    @staticmethod
    def get_recent(
        session: Session,
        tenant_id: Optional[str] = None,
        days: int = 7,
        limit: int = 500,
    ) -> list[CollectionMetric]:
        """최근 N일 수집 메트릭 (admin 패널 raw 뷰)."""
        since = _today_start_utc() - timedelta(days=days)
        query = session.query(CollectionMetric).filter(
            CollectionMetric.collected_at >= since
        )
        if tenant_id:
            query = query.filter(CollectionMetric.tenant_id == tenant_id)
        return (
            query
            .order_by(CollectionMetric.collected_at.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_daily_summary(
        session: Session,
        tenant_id: str,
        days: int = 7,
    ) -> list[dict]:
        """일자 × data_type 별 합계/평균.

        Returns: [{date, data_type, n, sum_raw, sum_final,
                   avg_latency_ms, fallback_n, error_n}, ...]
        """
        since = _today_start_utc() - timedelta(days=days)
        rows = (
            session.query(
                func.date(CollectionMetric.collected_at).label("date"),
                CollectionMetric.data_type,
                func.count(CollectionMetric.id).label("n"),
                func.sum(CollectionMetric.raw_count).label("sum_raw"),
                func.sum(CollectionMetric.final_count).label("sum_final"),
                func.avg(CollectionMetric.latency_ms).label("avg_latency_ms"),
                func.sum(
                    func.cast(CollectionMetric.fallback_used, Integer)
                ).label("fallback_n"),
                func.sum(
                    func.cast(
                        CollectionMetric.error.isnot(None), Integer
                    )
                ).label("error_n"),
            )
            .filter(
                and_(
                    CollectionMetric.tenant_id == tenant_id,
                    CollectionMetric.collected_at >= since,
                )
            )
            .group_by(
                func.date(CollectionMetric.collected_at),
                CollectionMetric.data_type,
            )
            .order_by(
                func.date(CollectionMetric.collected_at).desc(),
                CollectionMetric.data_type.asc(),
            )
            .all()
        )
        return [
            {
                "date": str(r.date),
                "data_type": r.data_type,
                "n": int(r.n or 0),
                "sum_raw": int(r.sum_raw or 0),
                "sum_final": int(r.sum_final or 0),
                "avg_latency_ms": int(r.avg_latency_ms or 0),
                "fallback_n": int(r.fallback_n or 0),
                "error_n": int(r.error_n or 0),
            }
            for r in rows
        ]

    @staticmethod
    def purge_older_than(session: Session, days: int = 90) -> int:
        """보존 기간 초과 메트릭 삭제. sent_articles 와 동일 정책."""
        cutoff = _today_start_utc() - timedelta(days=days)
        deleted = (
            session.query(CollectionMetric)
            .filter(CollectionMetric.collected_at < cutoff)
            .delete(synchronize_session=False)
        )
        return int(deleted or 0)
