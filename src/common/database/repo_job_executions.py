"""스케줄 잡 실행 이력(JobExecution) 저장소 — 멱등 가드 · catch-up 판정 (#4)."""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session
from ..timeutil import today_kst
from .models import JobExecution

logger = logging.getLogger(__name__)


class JobExecutionRepository:
    """스케줄 잡 실행 이력 저장소"""

    @staticmethod
    def _get(session: Session, tenant_id: str, job_key: str,
             newsletter_type: str, run_date: date) -> Optional[JobExecution]:
        return session.query(JobExecution).filter(
            JobExecution.tenant_id == tenant_id,
            JobExecution.job_key == job_key,
            JobExecution.newsletter_type == newsletter_type,
            JobExecution.run_date == run_date,
        ).first()

    @staticmethod
    def has_succeeded(session: Session, tenant_id: str, job_key: str,
                      newsletter_type: str, run_date: date) -> bool:
        row = JobExecutionRepository._get(
            session, tenant_id, job_key, newsletter_type, run_date
        )
        return row is not None and row.status == "success"

    @staticmethod
    def has_any(session: Session, tenant_id: str) -> bool:
        """이력 기준선 존재 여부 — 도입 직후(이력 0건) catch-up 오발동 방지용."""
        return session.query(JobExecution.id).filter(
            JobExecution.tenant_id == tenant_id
        ).first() is not None

    @staticmethod
    def mark_started(session: Session, tenant_id: str, job_key: str,
                     newsletter_type: str, run_date: date) -> JobExecution:
        """실행 시작 기록 — 같은 키가 있으면 attempts 를 올려 재사용."""
        row = JobExecutionRepository._get(
            session, tenant_id, job_key, newsletter_type, run_date
        )
        if row is None:
            row = JobExecution(
                tenant_id=tenant_id, job_key=job_key,
                newsletter_type=newsletter_type, run_date=run_date,
                attempts=0,
            )
            session.add(row)
        row.status = "running"
        row.attempts = (row.attempts or 0) + 1
        row.error_message = None
        row.started_at = datetime.utcnow()
        row.finished_at = None
        session.flush()
        return row

    @staticmethod
    def mark_finished(session: Session, tenant_id: str, job_key: str,
                      newsletter_type: str, run_date: date, success: bool,
                      error_message: Optional[str] = None) -> None:
        row = JobExecutionRepository._get(
            session, tenant_id, job_key, newsletter_type, run_date
        )
        if row is None:
            return
        row.status = "success" if success else "failed"
        row.error_message = (error_message or None) and error_message[:500]
        row.finished_at = datetime.utcnow()

    @staticmethod
    def list_for_date(session: Session, run_date: date) -> list[dict]:
        rows = session.query(JobExecution).filter(
            JobExecution.run_date == run_date
        ).order_by(JobExecution.tenant_id, JobExecution.started_at).all()
        return [
            {
                "tenant_id": r.tenant_id,
                "job_key": r.job_key,
                "newsletter_type": r.newsletter_type,
                "status": r.status,
                "attempts": r.attempts,
                "error": r.error_message,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in rows
        ]

    @staticmethod
    def purge_older_than(session: Session, days: int = 30) -> int:
        cutoff = today_kst() - timedelta(days=days)
        deleted = (
            session.query(JobExecution)
            .filter(JobExecution.run_date < cutoff)
            .delete(synchronize_session=False)
        )
        return int(deleted or 0)
