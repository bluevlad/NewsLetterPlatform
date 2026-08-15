"""뉴스레터 아카이브(NewsletterArchive) 저장소. P1b 분해(2026-08-15)로 repository.py 에서 분리."""

import logging
from datetime import datetime, date
from typing import Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session
from .models import NewsletterArchive

logger = logging.getLogger(__name__)


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
