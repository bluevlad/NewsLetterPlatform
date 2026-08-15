"""데이터베이스 저장소 — 하위 호환 facade.

P1b(2026-08-15): 1,460줄 단일 파일을 engine / migrations / 애그리게잇별
repo_* 모듈로 분해했다. 기존 호출부(`from ...repository import X`)를 깨지
않도록 모든 이름을 재노출한다. 신규 코드는 개별 모듈 직접 import 를 권장.
"""

from ..timeutil import (  # noqa: F401 — 구 코드 호환 재노출
    KST as _KST,
    KST_DATE_MODIFIER as _KST_DATE_MODIFIER,
    today_start_utc as _today_start_utc,
)
from .engine import init_db, get_session, get_session_factory  # noqa: F401
from .migrations import (  # noqa: F401
    run_all_migrations,
    _migrate_send_history_newsletter_type,
    _migrate_send_history_send_mode,
    _migrate_subscriber_send_slot,
    _migrate_sent_articles_company_name,
    _migrate_collection_metrics,
    _migrate_subscriber_persona_columns,
    _migrate_email_verification_signup_meta,
)
from .repo_subscribers import SubscriberRepository  # noqa: F401
from .repo_topic_requests import SubscriberTopicRequestRepository  # noqa: F401
from .repo_send_history import SendHistoryRepository  # noqa: F401
from .repo_collected_data import CollectedDataRepository  # noqa: F401
from .repo_archives import NewsletterArchiveRepository  # noqa: F401
from .repo_verifications import EmailVerificationRepository  # noqa: F401
from .repo_bounce import BounceLogRepository  # noqa: F401
from .repo_sent_articles import SentArticleRepository  # noqa: F401
from .repo_metrics import CollectionMetricRepository  # noqa: F401

__all__ = [
    "init_db", "get_session", "get_session_factory", "run_all_migrations",
    "SubscriberRepository", "SubscriberTopicRequestRepository",
    "SendHistoryRepository", "CollectedDataRepository",
    "NewsletterArchiveRepository", "EmailVerificationRepository",
    "BounceLogRepository", "SentArticleRepository", "CollectionMetricRepository",
]
