"""이메일 인증(EmailVerification) 저장소. P1b 분해(2026-08-15)로 repository.py 에서 분리."""

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session
from ..timeutil import today_start_utc as _today_start_utc
from .models import EmailVerification, VerificationType

logger = logging.getLogger(__name__)


class EmailVerificationRepository:
    """이메일 인증 저장소"""

    @staticmethod
    def create(session: Session, tenant_id: str, email: str, name: str,
               code: str, verification_type: VerificationType,
               expires_at: datetime,
               signup_meta: Optional[str] = None) -> EmailVerification:
        verification = EmailVerification(
            tenant_id=tenant_id,
            email=email,
            name=name,
            code=code,
            verification_type=verification_type,
            expires_at=expires_at,
            signup_meta=signup_meta,
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

    @staticmethod
    def purge_older_than(session: Session, days: int = 30) -> int:
        """보존 기간(기본 30일) 초과 인증 요청 삭제 — 인증 코드·가입 메타(PII) 최소 보관 (#4)."""
        cutoff = _today_start_utc() - timedelta(days=days)
        deleted = (
            session.query(EmailVerification)
            .filter(EmailVerification.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        return int(deleted or 0)
