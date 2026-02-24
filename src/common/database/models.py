"""
NewsLetterPlatform 데이터베이스 모델 정의
"""

from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Boolean,
    DateTime,
    Enum,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class VerificationType(PyEnum):
    """인증 유형"""
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"


class Subscriber(Base):
    """구독자"""
    __tablename__ = "subscribers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(50), nullable=False, index=True)
    email = Column(String(255), nullable=False)
    name = Column(String(100))
    unsubscribe_token = Column(String(64), unique=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_subscriber_tenant_email"),
        Index("idx_subscriber_tenant_active", "tenant_id", "is_active"),
    )

    def __repr__(self):
        return f"<Subscriber(tenant={self.tenant_id}, email='{self.email}')>"


class SendHistory(Base):
    """이메일 발송 이력"""
    __tablename__ = "send_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(50), nullable=False, index=True)
    subscriber_id = Column(Integer, nullable=False)
    subject = Column(String(500))
    is_success = Column(Boolean, default=False)
    error_message = Column(Text)
    sent_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_send_history_tenant_date", "tenant_id", "sent_at"),
        Index("idx_send_history_subscriber", "subscriber_id"),
    )

    def __repr__(self):
        return f"<SendHistory(tenant={self.tenant_id}, subscriber_id={self.subscriber_id})>"


class CollectedData(Base):
    """API 수집 데이터 캐시"""
    __tablename__ = "collected_data"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(50), nullable=False, index=True)
    data_type = Column(String(50), nullable=False)
    data_json = Column(Text, nullable=False)
    collected_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_collected_tenant_type", "tenant_id", "data_type"),
        Index("idx_collected_at", "collected_at"),
    )

    def __repr__(self):
        return f"<CollectedData(tenant={self.tenant_id}, type={self.data_type})>"


class JobExecution(Base):
    """스케줄러 Job 실행 이력 (멱등성 보장용)"""
    __tablename__ = "job_executions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(100), nullable=False)
    tenant_id = Column(String(50), nullable=False)
    execution_date = Column(String(10), nullable=False)  # YYYY-MM-DD
    status = Column(String(20), nullable=False, default="running")  # running, success, failed
    error_message = Column(Text)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime)

    __table_args__ = (
        UniqueConstraint("job_id", "tenant_id", "execution_date", name="uq_job_execution_daily"),
        Index("idx_job_exec_status", "status"),
        Index("idx_job_exec_date", "execution_date"),
    )

    def __repr__(self):
        return f"<JobExecution(job={self.job_id}, tenant={self.tenant_id}, date={self.execution_date})>"


class EmailVerification(Base):
    """이메일 인증 코드"""
    __tablename__ = "email_verifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(50), nullable=False, index=True)
    email = Column(String(255), nullable=False)
    name = Column(String(100))
    code = Column(String(6), nullable=False)
    verification_type = Column(Enum(VerificationType), default=VerificationType.SUBSCRIBE)
    is_verified = Column(Boolean, default=False)
    attempts = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)

    __table_args__ = (
        Index("idx_verification_tenant_email", "tenant_id", "email"),
        Index("idx_verification_expires", "expires_at"),
    )

    def __repr__(self):
        return f"<EmailVerification(tenant={self.tenant_id}, email='{self.email}')>"
