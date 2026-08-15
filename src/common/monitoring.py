"""콘텐츠 헬스 판정 — liveness 를 넘어 "내용이 맞는가"를 본다.

배경(설계 진단 P2): 2026년 운영 사고 5건(수집 중단·dedup 오염·구독 폭탄·
동일 콘텐츠 4일·stale 재발송)은 전부 프로세스는 살아 있고 발송 로그는
성공인 무음 실패였다. 기존 /api/health 는 liveness 만 보므로, 이 모듈이
테넌트별 콘텐츠 지표를 산출해 /api/health/content 로 노출한다.

지표 (테넌트별):
  - collect_age_hours: daily 캐시 중 가장 오래된 수집 경과 (>24h → warn)
  - last_normal_send:  마지막 정식 발송(send_mode='normal') KST 날짜
  - duplicate_archives: 최근 daily 아카이브 2건의 fingerprint 동일 여부
                        (동일 콘텐츠 반복 발송 사고의 사후 검출)
  - dedup_pool_size:   sent_articles 최근 윈도 기사 수 (풀 고갈 조기 신호)
"""

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from .database.repo_archives import NewsletterArchiveRepository
from .database.repo_collected_data import CollectedDataRepository
from .database.repo_send_history import SendHistoryRepository
from .database.repo_sent_articles import SentArticleRepository
from .timeutil import KST
from ..tenant.registry import get_registry

logger = logging.getLogger(__name__)

COLLECT_STALE_THRESHOLD_HOURS = 24


def _fingerprint(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def _tenant_content_health(session, tenant) -> dict:
    tid = tenant.tenant_id
    warns: list[str] = []

    # 1) 수집 신선도 (daily 지원 테넌트만)
    collect_age_hours = None
    if "daily" in tenant.supported_frequencies:
        collected = CollectedDataRepository.get_all_latest_with_time(session, tid)
        ages = [
            (datetime.utcnow() - collected_at).total_seconds() / 3600
            for data_type, (_, collected_at) in collected.items()
            if collected_at
            and not data_type.startswith(("weekly_", "monthly_"))
        ]
        if ages:
            collect_age_hours = round(max(ages), 1)
            if collect_age_hours > COLLECT_STALE_THRESHOLD_HOURS:
                warns.append(f"collect_stale({collect_age_hours}h)")
        else:
            warns.append("no_collected_data")

    # 2) 마지막 정식 발송 (KST 날짜)
    primary_type = (
        "daily" if "daily" in tenant.supported_frequencies
        else tenant.supported_frequencies[0]
    )
    last_send_at = SendHistoryRepository.get_last_normal_send_at(
        session, tid, newsletter_type=primary_type
    )
    last_normal_send = None
    if last_send_at:
        last_normal_send = (
            last_send_at.replace(tzinfo=timezone.utc)
            .astimezone(KST).date().isoformat()
        )

    # 3) 최근 daily 아카이브 2건 지문 비교 (동일 콘텐츠 반복 검출)
    duplicate_archives = False
    if "daily" in tenant.supported_frequencies:
        recent = NewsletterArchiveRepository.get_recent(session, tid, "daily", 2)
        if len(recent) == 2 and _fingerprint(recent[0].html_content) == _fingerprint(recent[1].html_content):
            duplicate_archives = True
            warns.append("duplicate_archives")

    # 4) dedup 풀 크기
    dedup_pool_size = None
    if tenant.dedup_recent_days:
        dedup_pool_size = len(
            SentArticleRepository.list_recent_article_ids(
                session, tid, days=tenant.dedup_recent_days
            )
        )

    return {
        "primary_type": primary_type,
        "collect_age_hours": collect_age_hours,
        "last_normal_send": last_normal_send,
        "duplicate_archives": duplicate_archives,
        "dedup_pool_size": dedup_pool_size,
        "status": "warn" if warns else "ok",
        "warnings": warns,
    }


def collect_content_health(session) -> dict:
    """전 테넌트 콘텐츠 헬스. 하나라도 warn 이면 전체 status=warn."""
    tenants = {}
    overall = "ok"
    for tenant in get_registry().get_all():
        try:
            info = _tenant_content_health(session, tenant)
        except Exception as e:
            logger.warning("[%s] 콘텐츠 헬스 산출 실패: %s", tenant.tenant_id, e)
            info = {"status": "error", "error": str(e)[:200]}
        tenants[tenant.tenant_id] = info
        if info.get("status") != "ok":
            overall = "warn"
    return {"status": overall, "tenants": tenants}
