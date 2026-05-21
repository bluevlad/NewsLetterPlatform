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
    Date,
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


class NewsletterType(PyEnum):
    """뉴스레터 유형"""
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    ADHOC = "adhoc"
    MANUAL = "manual"


class Subscriber(Base):
    """구독자"""
    __tablename__ = "subscribers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(50), nullable=False, index=True)
    email = Column(String(255), nullable=False)
    name = Column(String(100))
    unsubscribe_token = Column(String(64), unique=True)
    is_active = Column(Boolean, default=True)
    send_slot = Column(String(20), nullable=True)  # 'early' | 'mid' | 'late' | NULL(=DEFAULT_SLOT)
    # --- 페르소나 적응형 뉴스레터 (N1) ---
    # persona_code NULL → 런타임 'patient' 폴백. AllergyInsight 페르소나 카탈로그가 정본이며
    # FK 가 아닌 문자열 스냅샷으로 보관 (페르소나 정의 변경에도 과거 의미 보존).
    persona_code = Column(String(30), nullable=True)
    purpose = Column(String(50), nullable=True)             # 수신 목적 (페르소나 보조)
    depth_level = Column(String(20), default="practical")   # 'expert' | 'practical' | 'general'
    interests = Column(Text, nullable=True)                 # JSON 배열 — 관심 알러젠 코드
    # ---
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_subscriber_tenant_email"),
        Index("idx_subscriber_tenant_active", "tenant_id", "is_active"),
        Index("idx_subscriber_tenant_slot", "tenant_id", "send_slot", "is_active"),
        Index("idx_subscriber_tenant_persona", "tenant_id", "persona_code", "is_active"),
    )

    def __repr__(self):
        return f"<Subscriber(tenant={self.tenant_id}, email='{self.email}')>"


class SubscriberTopicRequest(Base):
    """콘텐츠 선택·변형 요청 이력 (UI 미러).

    정본 로그는 AllergyInsight `newsletter_topic_requests` 이며, NLP 는 수신자
    화면 표시·재요청용 최소 미러만 보관한다. (PERSONA_ADAPTIVE_NEWSLETTER_SPEC §2.2)
    """
    __tablename__ = "subscriber_topic_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(50), nullable=False, index=True)
    subscriber_id = Column(Integer, nullable=False)
    request_id = Column(String(64), unique=True, nullable=False)  # NLP 발급 UUID v4 — 멱등 키
    request_type = Column(String(20), nullable=False)             # 'select' | 'transform'
    topic = Column(String(500))
    # 'covered' | 'expandable' | 'unsupported' | 'pending'
    coverage = Column(String(20), default="pending", nullable=False)
    job_id = Column(String(64), nullable=True)                    # expandable 비동기 job
    result_json = Column(Text, nullable=True)                     # covered/콜백 결과 스냅샷
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("idx_topic_req_subscriber", "tenant_id", "subscriber_id", "created_at"),
        Index("idx_topic_req_job", "job_id"),
    )

    def __repr__(self):
        return f"<SubscriberTopicRequest(req={self.request_id}, coverage={self.coverage})>"


class SendHistory(Base):
    """이메일 발송 이력"""
    __tablename__ = "send_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(50), nullable=False, index=True)
    subscriber_id = Column(Integer, nullable=False)
    subject = Column(String(500))
    newsletter_type = Column(String(20), default="daily", nullable=False)
    # 발송 모드: 'normal'(정식 발송) | 'weekend_test'(주말 관리자 테스트)
    # | 추후 'manual'/'preview' 등 확장 가능. 통계 집계 시 'normal'만 필터링.
    send_mode = Column(String(20), default="normal", nullable=False)
    is_success = Column(Boolean, default=False)
    error_message = Column(Text)
    sent_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_send_history_tenant_date", "tenant_id", "sent_at"),
        Index("idx_send_history_subscriber", "subscriber_id"),
        Index("idx_send_history_type", "tenant_id", "newsletter_type", "sent_at"),
        Index("idx_send_history_mode", "tenant_id", "send_mode", "sent_at"),
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


class CollectedDataHistory(Base):
    """일일 수집 데이터 이력 보관 (주간/월간 집계용)"""
    __tablename__ = "collected_data_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(50), nullable=False, index=True)
    data_type = Column(String(50), nullable=False)
    data_json = Column(Text, nullable=False)
    collected_date = Column(Date, nullable=False)
    collected_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("tenant_id", "data_type", "collected_date",
                         name="uq_history_tenant_type_date"),
        Index("idx_history_tenant_date", "tenant_id", "collected_date"),
    )

    def __repr__(self):
        return f"<CollectedDataHistory(tenant={self.tenant_id}, type={self.data_type}, date={self.collected_date})>"


class NewsletterArchive(Base):
    """뉴스레터 아카이브 - 발송된 뉴스레터 HTML 보관"""
    __tablename__ = "newsletter_archives"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(50), nullable=False, index=True)
    newsletter_type = Column(String(20), nullable=False)
    subject = Column(String(500))
    html_content = Column(Text, nullable=False)
    sent_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "newsletter_type", "sent_date",
            name="uq_archive_tenant_type_date"
        ),
        Index("idx_archive_tenant_date", "tenant_id", "sent_date"),
    )

    def __repr__(self):
        return f"<NewsletterArchive(tenant={self.tenant_id}, type={self.newsletter_type}, date={self.sent_date})>"


class SentArticle(Base):
    """뉴스레터에 포함되어 발송된 기사 이력 (교차일 dedup용)

    AllergyInsight 등에서 동일 article_id 가 수일간 반복 선정되는 것을
    차단하기 위해, 발송 성공 시 테넌트·섹션 단위로 기록한다.
    """
    __tablename__ = "sent_articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(50), nullable=False)
    article_id = Column(Integer, nullable=False)
    article_url = Column(String(1000))
    section = Column(String(30), nullable=False)
    sent_date = Column(Date, nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    company_name = Column(String(200))

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "article_id", "section", "sent_date",
            name="uq_sent_articles_dedup"
        ),
        Index("idx_sent_articles_tenant_time", "tenant_id", "sent_at"),
        Index("idx_sent_articles_lookup", "tenant_id", "article_id"),
        Index("idx_sent_articles_company", "tenant_id", "company_name"),
    )

    def __repr__(self):
        return (
            f"<SentArticle(tenant={self.tenant_id}, article_id={self.article_id}, "
            f"section={self.section}, date={self.sent_date})>"
        )


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
    # 구독 폼에서 고른 페르소나 선택을 인증 단계 너머로 운반 (N1).
    # JSON: {persona_code, purpose, depth_level, interests}
    signup_meta = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_verification_tenant_email", "tenant_id", "email"),
        Index("idx_verification_expires", "expires_at"),
    )

    def __repr__(self):
        return f"<EmailVerification(tenant={self.tenant_id}, email='{self.email}')>"


class CollectionMetric(Base):
    """수집 단계 메트릭 (테넌트 × 데이터 타입 × 엔드포인트)

    `run_collect_job` 1회당 collector 가 호출한 각 API 엔드포인트 단위로 1행.
    회귀 감지/V1 digest 실효성 측정/staleness 알림의 공통 입력 테이블.
    """
    __tablename__ = "collection_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(50), nullable=False)
    # 'daily' | 'weekly' | 'monthly' — run_collect_job 의 newsletter_type
    newsletter_type = Column(String(20), default="daily", nullable=False)
    # 수집 단위 (예: 'headlines', 'company_digest', 'papers', 'github_releases')
    data_type = Column(String(50), nullable=False)
    api_path = Column(String(200))                  # 실제 호출 경로 (POST alias 면 alias 경로)
    raw_count = Column(Integer, default=0, nullable=False)
    final_count = Column(Integer, default=0, nullable=False)
    excluded_by_ids = Column(Integer, default=0, nullable=False)
    excluded_by_companies = Column(Integer, default=0, nullable=False)
    effective_days = Column(Integer)                # 실제 사용된 lookback 윈도 (nullable)
    fallback_used = Column(Boolean, default=False, nullable=False)  # 풀 확장/POST alias 발동
    latency_ms = Column(Integer, default=0, nullable=False)
    error = Column(String(500))                     # 실패 시 메시지 (nullable)
    collected_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_collection_metrics_tenant_time",
              "tenant_id", "collected_at"),
        Index("idx_collection_metrics_type_time",
              "tenant_id", "data_type", "collected_at"),
        Index("idx_collection_metrics_fallback",
              "tenant_id", "fallback_used", "collected_at"),
    )

    def __repr__(self):
        return (
            f"<CollectionMetric(tenant={self.tenant_id}, type={self.data_type}, "
            f"raw={self.raw_count}, final={self.final_count}, "
            f"fallback={self.fallback_used})>"
        )


class BounceLog(Base):
    """이메일 bounce 이력 (NDR 자동 처리)

    IMAP으로 운영자 inbox에서 수집한 NDR을 파싱하여 적재.
    hard bounce 시 동일 email의 모든 subscribers row가 비활성화되며
    request_subscribe 진입 단계에서 사전 차단의 근거로 사용된다.
    """
    __tablename__ = "bounce_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), nullable=False, index=True)
    bounce_type = Column(String(10), nullable=False)  # 'hard' | 'soft'
    smtp_code = Column(String(20))                     # e.g. '550 5.1.1'
    diagnostic = Column(Text)                          # NDR 본문 발췌
    ndr_message_id = Column(String(255), unique=True)  # IMAP Message-ID — 재처리 방지
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("idx_bounce_email_type_created", "email", "bounce_type", "created_at"),
    )

    def __repr__(self):
        return f"<BounceLog(email='{self.email}', type={self.bounce_type}, code={self.smtp_code})>"
