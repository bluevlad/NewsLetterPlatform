"""발송 이력(SendHistory) 저장소 — dedup 조회·통계 집계 포함. P1b 분해(2026-08-15)로 repository.py 에서 분리."""

import logging
from datetime import datetime, date, timedelta
from typing import Optional

from sqlalchemy import and_, func, Integer
from sqlalchemy.orm import Session
from ..timeutil import KST_DATE_MODIFIER as _KST_DATE_MODIFIER, today_start_utc as _today_start_utc
from .models import SendHistory

logger = logging.getLogger(__name__)


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
                                      newsletter_type: str = "daily",
                                      subject: Optional[str] = None) -> set[int]:
        """당일 발송 완료된 구독자 ID 일괄 조회 (N+1 방지, newsletter_type별 분리)

        Args:
            subject: 지정 시 해당 제목의 발송만 조회. adhoc 은 같은 날 서로 다른
                     뉴스레터를 여러 번 보낼 수 있으므로 제목까지 매칭해야
                     두 번째 adhoc 이 첫 발송 수신자를 잘못 스킵하지 않는다.
        """
        today_start = _today_start_utc()
        conditions = [
            SendHistory.tenant_id == tenant_id,
            SendHistory.newsletter_type == newsletter_type,
            SendHistory.sent_at >= today_start,
            SendHistory.is_success == True,
            # 정식 발송만 dedup 대상 — stale/duplicate alert 나 휴일 테스트가
            # newsletter_type='daily' 로 기록돼도 이후 정상 발송을 막지 않는다.
            SendHistory.send_mode == "normal",
        ]
        if subject is not None:
            conditions.append(SendHistory.subject == subject)
        rows = (
            session.query(SendHistory.subscriber_id)
            .filter(and_(*conditions))
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
        """최근 N일 일별 발송 요약 (KST 날짜 기준).

        발송은 KST 06:40~09:30 = UTC 전날 밤이므로, UTC 로 날짜를 자르면
        모든 발송이 전날로 귀속된다. KST 보정 후 그룹핑한다.
        """
        kst_date = func.date(SendHistory.sent_at, _KST_DATE_MODIFIER)
        since = _today_start_utc() - timedelta(days=days)
        rows = (
            session.query(
                kst_date.label("date"),
                func.count(SendHistory.id).label("total"),
                func.sum(func.cast(SendHistory.is_success, Integer)).label("success"),
            )
            .filter(
                and_(
                    SendHistory.tenant_id == tenant_id,
                    SendHistory.sent_at >= since,
                )
            )
            .group_by(kst_date)
            .order_by(kst_date.desc())
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
        """전체 테넌트 최근 N일 일별 발송 요약 (KST 날짜 기준)"""
        kst_date = func.date(SendHistory.sent_at, _KST_DATE_MODIFIER)
        since = _today_start_utc() - timedelta(days=days)
        rows = (
            session.query(
                kst_date.label("date"),
                func.count(SendHistory.id).label("total"),
                func.sum(func.cast(SendHistory.is_success, Integer)).label("success"),
            )
            .filter(SendHistory.sent_at >= since)
            .group_by(kst_date)
            .order_by(kst_date.desc())
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
                    SendHistory.is_success == True,
                    SendHistory.send_mode == "normal",  # 정식 발송만 dedup 대상
                )
            )
            .distinct()
            .all()
        )
        return {row[0] for row in rows}
