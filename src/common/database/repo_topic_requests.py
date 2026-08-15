"""페르소나 토픽 요청 미러(SubscriberTopicRequest) 저장소. P1b 분해(2026-08-15)로 repository.py 에서 분리."""

import logging
from typing import Optional

from sqlalchemy import and_
from sqlalchemy.orm import Session
from .models import SubscriberTopicRequest

logger = logging.getLogger(__name__)


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
