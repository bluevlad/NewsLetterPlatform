"""바운스 로그(BounceLog) 저장소. P1b 분해(2026-08-15)로 repository.py 에서 분리."""

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session
from .models import BounceLog

logger = logging.getLogger(__name__)


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
