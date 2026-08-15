"""구독자 참여 이벤트(EngagementEvent) 저장소 (P2)."""

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ..timeutil import today_start_utc as _today_start_utc
from .models import EngagementEvent, Subscriber

logger = logging.getLogger(__name__)


class EngagementEventRepository:
    """open / click / feedback 이벤트 기록·릴레이 관리."""

    @staticmethod
    def record(
        session: Session, tenant_id: str, subscriber_id: int,
        event_type: str,
        newsletter_type: str = "daily",
        target_url: Optional[str] = None,
        section: Optional[str] = None,
        feedback_value: Optional[str] = None,
    ) -> EngagementEvent:
        """이벤트 기록. persona_code 는 이벤트 시점 스냅샷으로 저장 (N4)."""
        persona_code = None
        sub = session.query(Subscriber).filter(
            Subscriber.id == subscriber_id
        ).first()
        if sub:
            persona_code = sub.persona_code

        event = EngagementEvent(
            tenant_id=tenant_id,
            subscriber_id=subscriber_id,
            event_type=event_type,
            newsletter_type=newsletter_type,
            target_url=(target_url or None),
            section=(section or None),
            feedback_value=(feedback_value or None),
            persona_code=persona_code,
        )
        session.add(event)
        session.flush()
        return event

    @staticmethod
    def list_unrelayed(session: Session, tenant_id: str,
                       limit: int = 500) -> list[EngagementEvent]:
        """릴레이 미완(relayed_at IS NULL) 이벤트 (오래된 순)."""
        return (
            session.query(EngagementEvent)
            .filter(
                EngagementEvent.tenant_id == tenant_id,
                EngagementEvent.relayed_at.is_(None),
            )
            .order_by(EngagementEvent.created_at.asc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def mark_relayed(session: Session, event_ids: list[int]) -> int:
        """릴레이 완료 마킹."""
        if not event_ids:
            return 0
        updated = (
            session.query(EngagementEvent)
            .filter(EngagementEvent.id.in_(event_ids))
            .update({"relayed_at": datetime.utcnow()},
                    synchronize_session=False)
        )
        return int(updated or 0)

    @staticmethod
    def daily_counts(session: Session, tenant_id: str,
                     days: int = 7) -> dict:
        """최근 N일 이벤트 타입별 건수 — 대시보드/트랙 E 입력용."""
        from sqlalchemy import func
        cutoff = _today_start_utc() - timedelta(days=days)
        rows = (
            session.query(
                EngagementEvent.event_type,
                func.count(EngagementEvent.id),
            )
            .filter(
                EngagementEvent.tenant_id == tenant_id,
                EngagementEvent.created_at >= cutoff,
            )
            .group_by(EngagementEvent.event_type)
            .all()
        )
        return {etype: count for etype, count in rows}

    @staticmethod
    def purge_older_than(session: Session, days: int = 90) -> int:
        """보존 기간 초과 이벤트 삭제 (sent_articles 와 동일 90일 정책)."""
        cutoff = _today_start_utc() - timedelta(days=days)
        deleted = (
            session.query(EngagementEvent)
            .filter(EngagementEvent.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        return int(deleted or 0)
