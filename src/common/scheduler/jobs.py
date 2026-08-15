"""
스케줄러 작업 정의
테넌트별 데이터 수집 및 뉴스레터 발송
"""

import asyncio
import hashlib
import logging
import time
from datetime import datetime, date, timedelta, timezone
from types import SimpleNamespace
from typing import Optional

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from ..database.repository import (
    get_session, CollectedDataRepository,
    SubscriberRepository, SendHistoryRepository,
    NewsletterArchiveRepository, SentArticleRepository,
    CollectionMetricRepository,
)
from ..database.models import CollectionMetric
from ..alerts import send_ops_alert
from ..delivery.gmail_sender import get_sender
from ..delivery.bounce_processor import run_bounce_processor
from ..holiday import KST, holiday_name, non_business_day_reason
from ..template.renderer import get_renderer
from .health import update_health
from .send_plan import (
    alert_plan, holiday_test_plan, manual_plan, normal_plan,
)
from .slots import DAILY_SLOTS, get_slot_time
from ...config import settings
from ...tenant.registry import get_registry

logger = logging.getLogger(__name__)

# 휴일 발송 정책: KST(UTC+9) 기준 주말(토·일)·공휴일이면 자동 발송 전체 스킵.
# HOLIDAY_ADMIN_TEST_ENABLED=true 일 때만 관리자 테스트 모드(early 슬롯 1회)로 발송.
# 판정(주말/법정 공휴일/EXTRA_HOLIDAYS)은 src/common/holiday.py 참조.
_HOLIDAY_TEST_SLOT = "early"  # 휴일 테스트 모드는 early 슬롯만 발송 (mid/late는 스킵)

# Stale-cache admin alert: 캐시 데이터가 24시간 초과면 일반 구독자 발송 중단,
# SUPER_ADMIN_EMAILS 에만 STALE 배너로 발송. (FRESHNESS_PLAN AC-8 / 트랙 F P6)
STALE_CACHE_THRESHOLD_SECONDS = 24 * 3600


def _html_fingerprint(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def _latest_collection_error(session, tenant_id: str) -> Optional[str]:
    """최근 24h 내 가장 최신 collection_metrics.error 메시지. STALE 배너 메타용."""
    since = datetime.utcnow() - timedelta(hours=24)
    row = (
        session.query(CollectionMetric.error)
        .filter(
            CollectionMetric.tenant_id == tenant_id,
            CollectionMetric.collected_at >= since,
            CollectionMetric.error.isnot(None),
        )
        .order_by(CollectionMetric.collected_at.desc())
        .first()
    )
    return row[0] if row else None


def _get_admin_recipients(session, tenant_id: str) -> list:
    """SUPER_ADMIN_EMAILS 를 발송 대상 객체 리스트로 변환.

    - 해당 테넌트에 admin 이메일이 구독자로 등록되어 있으면 그 Subscriber 사용
    - 등록되어 있지 않으면 SimpleNamespace 로 대체 (id=0, unsubscribe 무효 토큰)
    """
    if not settings.super_admin_emails:
        return []
    admins = [
        e.strip() for e in settings.super_admin_emails.split(",")
        if e.strip()
    ]
    recipients = []
    for email in admins:
        sub = SubscriberRepository.get_active_by_email(session, tenant_id, email)
        if sub:
            recipients.append(sub)
        else:
            recipients.append(SimpleNamespace(
                id=0,
                email=email,
                unsubscribe_token="weekend-test-noop",
            ))
    return recipients


def _personalize_html(
    html: str, tenant_id: str, unsubscribe_token: str,
    subscriber_id: int = None,
) -> str:
    """수신자별 placeholder 치환 — 모든 발송 경로(정규·adhoc·웰컴)가 공유.

    __UNSUBSCRIBE_URL__ / __PERSONA_REQUEST_URL__ /
    __FEEDBACK_UP_URL__ / __FEEDBACK_DOWN_URL__ 을 치환하고,
    subscriber_id 가 있으면 open pixel 주입 + (env 활성 시) 클릭 링크
    재작성을 수행한다 (P2 engagement).
    템플릿에 placeholder 가 없으면 replace 는 무해한 no-op.
    경로별로 치환 목록이 어긋나는 드리프트를 막기 위해 반드시 이 함수를
    통해 치환한다.
    """
    from ..engagement import (
        make_feedback_url, make_open_pixel, rewrite_click_links,
    )

    unsubscribe_url = (
        f"{settings.web_base_url}/{tenant_id}"
        f"/unsubscribe/token/{unsubscribe_token}"
    )
    persona_request_url = (
        f"{settings.web_base_url}/{tenant_id}"
        f"/persona/request?token={unsubscribe_token}"
    )
    html = (
        html.replace("__UNSUBSCRIBE_URL__", unsubscribe_url)
        .replace("__PERSONA_REQUEST_URL__", persona_request_url)
    )

    if subscriber_id:
        html = (
            html.replace(
                "__FEEDBACK_UP_URL__",
                make_feedback_url(tenant_id, subscriber_id, "up"),
            )
            .replace(
                "__FEEDBACK_DOWN_URL__",
                make_feedback_url(tenant_id, subscriber_id, "down"),
            )
        )
        # 클릭 추적 (env 게이트, 기본 off) — pixel 주입 전에 수행해
        # 추적 픽셀 URL 자체는 재작성 대상에서 제외
        html = rewrite_click_links(html, tenant_id, subscriber_id)
        pixel = make_open_pixel(tenant_id, subscriber_id)
        if pixel:
            if "</body>" in html:
                html = html.replace("</body>", pixel + "</body>", 1)
            else:
                html += pixel
    else:
        # 미리보기·구독자 미상 경로 — 피드백 placeholder 를 무해 처리
        html = (
            html.replace("__FEEDBACK_UP_URL__", "#")
            .replace("__FEEDBACK_DOWN_URL__", "#")
        )
    return html


def _deliver_and_record(
    tenant_id: str, subject: str,
    messages: list, recipients: list,
    history_type: str, send_mode: str, log_prefix: str,
) -> tuple[int, int]:
    """SMTP 배치 발송 + 행 단위 이력 커밋 + 실패분 1회 재시도.

    발송(비가역)은 DB 세션 밖에서 수행하고, 이력은 행 단위로 즉시 커밋한다
    — 기록 도중 오류가 나도 이미 커밋된 행은 유지되어 재발송을 막는다.

    Args:
        recipients: messages 와 병렬인 (subscriber_id, email) 튜플 리스트.

    Returns:
        (sent_count, failed_count) — failed_count 는 재시도까지 실패한 건수.
    """
    sender = get_sender()
    results = sender.send_batch_efficient(messages)

    failed_items: list[tuple[int, str, dict]] = []
    sent_count = 0
    with get_session() as session:
        for (sub_id, email), msg, result in zip(recipients, messages, results):
            SendHistoryRepository.create(
                session, tenant_id, sub_id,
                subject, result.success, result.error_message,
                newsletter_type=history_type,
                send_mode=send_mode,
            )
            session.commit()
            if result.success:
                sent_count += 1
            else:
                failed_items.append((sub_id, email, msg))
                logger.error(
                    f"{log_prefix} 발송 실패: {email} - {result.error_message}"
                )

    failed_count = 0
    if failed_items:
        logger.info(f"{log_prefix} {len(failed_items)}건 재시도 (5초 후)")
        time.sleep(5)

        retry_messages = [msg for _, _, msg in failed_items]
        retry_results = sender.send_batch_efficient(retry_messages)

        with get_session() as session:
            for (sub_id, email, _), retry_result in zip(failed_items, retry_results):
                if retry_result.success:
                    SendHistoryRepository.create(
                        session, tenant_id, sub_id,
                        subject, True, None,
                        newsletter_type=history_type,
                        send_mode=send_mode,
                    )
                    session.commit()
                    sent_count += 1
                    logger.info(f"{log_prefix} 재시도 발송 성공: {email}")
                else:
                    failed_count += 1
                    logger.error(
                        f"{log_prefix} 재시도 발송 실패: {email} - "
                        f"{retry_result.error_message}"
                    )
    return sent_count, failed_count


def _get_period_range(newsletter_type: str) -> tuple[date, date]:
    """뉴스레터 유형별 집계 기간 계산

    Returns:
        (date_from, date_to) - 집계 시작일, 종료일
    """
    # KST 명시 — 컨테이너 TZ 설정에 의존하지 않는다
    today = datetime.now(KST).date()
    if newsletter_type == "weekly":
        # 이번 주 월요일~오늘 (금요일 발송 기준)
        date_from = today - timedelta(days=today.weekday())  # 월요일
        date_to = today
        return date_from, date_to
    elif newsletter_type == "monthly":
        if today.day == 1:
            # 1일 발송: 지난달 1일~말일
            date_to = today - timedelta(days=1)
            date_from = date_to.replace(day=1)
        else:
            # 말일 발송: 이번달 1일~오늘
            date_from = today.replace(day=1)
            date_to = today
        return date_from, date_to
    else:
        # daily: 오늘
        return today, today


def _get_period_start_for_dedup(newsletter_type: str) -> datetime:
    """중복 방지용 기간 시작 시각 계산 (KST 기준 → naive UTC 반환)

    sent_at이 UTC로 저장되므로, KST 기준 기간 시작을 UTC로 환산하여 반환한다.
    """
    from datetime import timezone
    today = datetime.now(KST).date()

    if newsletter_type == "weekly":
        # 이번주 월요일 00:00 KST → UTC
        monday = today - timedelta(days=today.weekday())
        start_kst = datetime.combine(monday, datetime.min.time()).replace(tzinfo=KST)
    elif newsletter_type == "monthly":
        # 이번달 1일 00:00 KST → UTC
        first = today.replace(day=1)
        start_kst = datetime.combine(first, datetime.min.time()).replace(tzinfo=KST)
    else:
        # daily: 오늘 00:00 KST → UTC
        start_kst = datetime.combine(today, datetime.min.time()).replace(tzinfo=KST)

    return start_kst.astimezone(timezone.utc).replace(tzinfo=None)


def _compute_catchup_days(session, tenant_id: str) -> Optional[int]:
    """휴일 catch-up 갭 판정 (P2 — HOLIDAY_SEND_POLICY Phase 2).

    오늘이 영업일이고 마지막 정식 daily 발송(send_mode='normal')과의 갭이
    2일 이상이면(=휴일 스킵으로 하루 이상 미발송) 갭 일수를 반환.
    수집기가 lookback 윈도를 그만큼 확장해 휴일 기간 소식을 포함한다.
    갭 없음·비영업일·발송 이력 없음이면 None.
    """
    today = datetime.now(KST).date()
    if non_business_day_reason(today) is not None:
        return None  # 오늘도 휴일 — catch-up 아님 (자동 발송 자체가 스킵됨)
    last_send_at = SendHistoryRepository.get_last_normal_send_at(
        session, tenant_id, newsletter_type="daily"
    )
    if not last_send_at:
        return None
    last_kst = (
        last_send_at.replace(tzinfo=timezone.utc).astimezone(KST).date()
    )
    gap = (today - last_kst).days
    return gap if gap >= 2 else None


def run_collect_job(tenant_id: str, newsletter_type: str = "daily") -> None:
    """데이터 수집 작업"""
    type_label = f"[{newsletter_type}]" if newsletter_type != "daily" else ""
    logger.info(f"[{tenant_id}]{type_label} 데이터 수집 시작")

    registry = get_registry()
    tenant = registry.get(tenant_id)
    if not tenant:
        logger.error(f"[{tenant_id}] 테넌트를 찾을 수 없습니다.")
        return

    try:
        if newsletter_type == "daily":
            # dedup 활성 테넌트는 최근 발송 이력을 exclude_ids/exclude_companies
            # 로 전달하여 수집/선정 단계에서 원천 제외.
            recent_ids: list[int] = []
            recent_companies: list[str] = []
            catchup_days = None
            with get_session() as session:
                if tenant.dedup_recent_days:
                    recent_ids = SentArticleRepository.list_recent_article_ids(
                        session, tenant_id, days=tenant.dedup_recent_days
                    )
                    recent_companies = (
                        SentArticleRepository.list_recent_company_names(
                            session, tenant_id, days=tenant.dedup_recent_days
                        )
                    )
                catchup_days = _compute_catchup_days(session, tenant_id)
            if recent_ids or recent_companies:
                logger.info(
                    f"[{tenant_id}] dedup: 최근 {tenant.dedup_recent_days}일 "
                    f"발송 기사 {len(recent_ids)}건 / 기업 {len(recent_companies)}건 "
                    "제외 대상"
                )
            if catchup_days:
                logger.info(
                    f"[{tenant_id}] 휴일 catch-up: 마지막 정식 발송 후 "
                    f"{catchup_days}일 경과 — 수집 윈도 확장"
                )
            collected = asyncio.run(
                tenant.collect_data(
                    exclude_ids=recent_ids or None,
                    exclude_companies=recent_companies or None,
                    catchup_days=catchup_days,
                )
            )
        else:
            # weekly/monthly: 요약 데이터 수집
            date_from, date_to = _get_period_range(newsletter_type)
            collected = asyncio.run(
                tenant.collect_summary_data(newsletter_type, date_from, date_to)
            )

        if not collected:
            logger.warning(
                f"[{tenant_id}]{type_label} 수집된 데이터가 없습니다. "
                "이전 캐시 데이터로 발송됩니다."
            )
            update_health("collect")
            return

        with get_session() as session:
            for data_type, data in collected.items():
                if newsletter_type == "daily":
                    # daily: 캐시 upsert + 이력 저장
                    CollectedDataRepository.upsert(session, tenant_id, data_type, data)
                    CollectedDataRepository.save_to_history(
                        session, tenant_id, data_type, data
                    )
                else:
                    # weekly/monthly: prefixed data_type으로 캐시
                    prefixed_type = f"{newsletter_type}_{data_type}"
                    CollectedDataRepository.upsert(
                        session, tenant_id, prefixed_type, data
                    )

        logger.info(f"[{tenant_id}]{type_label} 데이터 수집 완료: {list(collected.keys())}")

    except Exception as e:
        logger.exception(
            f"[{tenant_id}]{type_label} 데이터 수집 중 오류: {e}. "
            "이전 캐시 데이터로 발송됩니다."
        )
        # 무음 실패 방지 — 수집 실패는 stale 재발송의 전조이므로 즉시 경보 (P2)
        send_ops_alert(
            f"{newsletter_type} 데이터 수집 실패",
            f"{str(e)[:300]}\n이전 캐시로 발송이 진행될 수 있습니다.",
            tenant_id=tenant_id,
        )

    # 수집 메트릭 영속화 — 성공/실패 무관(부분 수집도 가시화).
    # collector 가 누적해 둔 _metrics 를 1회 drain 후 DB 기록한다.
    try:
        collection_metrics = tenant.extract_collection_metrics()
        if collection_metrics:
            with get_session() as session:
                CollectionMetricRepository.record_many(
                    session, tenant_id, newsletter_type, collection_metrics
                )
            logger.info(
                f"[{tenant_id}]{type_label} collection_metrics 기록: "
                f"{len(collection_metrics)}건"
            )
    except Exception as e:
        logger.warning(
            f"[{tenant_id}]{type_label} collection_metrics 기록 실패: {e}"
        )

    update_health("collect")


def run_send_job(
    tenant_id: str, newsletter_type: str = "daily", manual: bool = False,
    slot: str = None
) -> None:
    """뉴스레터 발송 작업 (배치 발송 + 실패 재시도)

    Args:
        manual: True이면 수동 발송 모드 — dedup 스킵, newsletter_type="manual"로 이력 저장.
                자동 스케줄 발송 이력(daily/weekly/monthly)에 영향을 주지 않는다.
        slot: 'early'/'mid'/'late' — 해당 슬롯 구독자만 발송. None이면 전체 활성 구독자.
              (수동 모드나 1회성 실행에 None 사용)
    """
    # 발송 정책은 SendPlan 하나로 결정 — 이후 코드는 plan 필드만 읽는다.
    plan = manual_plan(newsletter_type) if manual else normal_plan(newsletter_type)
    mode_label = "[manual]" if manual else ""
    slot_label = f"[slot={slot}]" if slot else ""
    type_label = f"[{newsletter_type}]" if newsletter_type != "daily" else ""
    log_prefix = f"[{tenant_id}]{mode_label}{type_label}{slot_label}"
    logger.info(f"{log_prefix} 뉴스레터 발송 시작")

    registry = get_registry()
    tenant = registry.get(tenant_id)
    if not tenant:
        logger.error(f"[{tenant_id}] 테넌트를 찾을 수 없습니다.")
        return

    # 휴일(주말·공휴일) 판정 — 자동 스케줄(=manual=False)에만 적용.
    # 주말=토/일, 공휴일=법정 공휴일(대체휴일 포함)+EXTRA_HOLIDAYS 지정일.
    today_kst = datetime.now(KST).date()
    nonbiz_reason = (
        non_business_day_reason(today_kst)
        if not manual and getattr(tenant, "weekend_test_mode", True)
        else None
    )
    if nonbiz_reason is not None:
        # 기본 정책: 휴일(토·일·공휴일)엔 자동 발송 전체 스킵 (관리자 테스트 포함).
        if not settings.holiday_admin_test_enabled:
            h_name = (
                holiday_name(today_kst) if nonbiz_reason == "holiday" else None
            )
            logger.info(
                f"{log_prefix} 휴일({nonbiz_reason}"
                + (f": {h_name}" if h_name else "")
                + ") — 자동 발송 스킵 (HOLIDAY_ADMIN_TEST_ENABLED=false)"
            )
            return
        # 테스트 모드(env 활성 시): 휴일엔 early 슬롯만 1회 관리자 테스트 발송.
        # mid/late 는 스킵.
        if slot and slot != _HOLIDAY_TEST_SLOT:
            logger.info(
                f"{log_prefix} 휴일({nonbiz_reason}) — early 슬롯만 관리자 테스트 발송, "
                f"{slot} 슬롯 스킵"
            )
            return
        plan = holiday_test_plan(newsletter_type, nonbiz_reason)
        log_prefix = f"{log_prefix}[{plan.send_mode}]"
        h_name = holiday_name(today_kst) if nonbiz_reason == "holiday" else None
        logger.info(
            f"{log_prefix} 휴일 관리자 테스트 모드로 발송"
            + (f" (공휴일: {h_name})" if h_name else "")
        )

    sender = get_sender()
    if not sender.is_configured:
        logger.warning(f"[{tenant_id}] Gmail 설정이 완료되지 않아 발송을 건너뜁니다.")
        return

    renderer = get_renderer()

    with get_session() as session:
        if newsletter_type == "daily":
            # 기존 daily 로직
            (context, template_name, subject,
             max_cache_age, oldest_collected_at) = _prepare_daily_send(
                session, tenant_id, tenant, type_label
            )

            # P6: 캐시 24h 초과 + 자동 정식 발송이면 stale alert 모드 진입.
            # 수동(manual)·휴일 테스트는 그대로 진행 — 운영자 의도.
            if (
                context is not None
                and plan.mode == "normal"
                and max_cache_age is not None
                and max_cache_age > STALE_CACHE_THRESHOLD_SECONDS
            ):
                plan = alert_plan(plan, "stale_admin_alert")
                hours = int(max_cache_age // 3600)
                log_prefix = f"{log_prefix}[stale_alert]"
                logger.warning(
                    f"{log_prefix} 캐시 {hours}h 초과 — 일반 구독자 발송 중단, "
                    f"SUPER_ADMIN_EMAILS 에만 STALE 배너 발송"
                )
                # 외부 경보 — Gmail(경보 이메일)과 독립된 채널 (P2)
                send_ops_alert(
                    "stale-cache 경보 — 구독자 발송 중단",
                    f"캐시 {hours}h 초과. 관리자에게만 STALE 배너 발송.",
                    tenant_id=tenant_id,
                )
                # 템플릿용 stale 메타데이터 주입 (배너 렌더)
                last_error = _latest_collection_error(session, tenant_id)
                context["stale_alert"] = {
                    "max_cache_age_hours": hours,
                    "last_collected_at": (
                        oldest_collected_at.isoformat()
                        if oldest_collected_at else None
                    ),
                    "last_error": last_error,
                }
                # 제목 prefix
                subject = f"[⚠️STALE] {subject}"
        else:
            # weekly/monthly 로직
            context, template_name, subject = _prepare_summary_send(
                session, tenant_id, tenant, newsletter_type, type_label
            )

        if context is None:
            return

        # pre-rendered HTML이 있으면 템플릿 렌더링 스킵
        if "prerendered_html" in context:
            html_content = context["prerendered_html"]
            logger.info(f"{log_prefix} pre-rendered HTML 사용 (템플릿 렌더링 스킵)")
        else:
            # HTML 렌더링
            try:
                html_content = renderer.render(template_name, context)
            except Exception as e:
                logger.error(f"{log_prefix} 템플릿 렌더링 실패: {e}")
                return

        # AC-9 duplicate-content 가드: daily 자동 발송에서 직전 archive 와 html 이
        # 동일하면 (백엔드 신규 유입 0건으로 어제와 같은 내용을 또 보내는 케이스)
        # 일반 구독자 발송 차단 + SUPER_ADMIN_EMAILS 에만 DUPLICATE 배너로 통지.
        # stale_alert 가 우선 발동했다면 plan.mode 가 이미 바뀌어 자동 스킵된다.
        # pre-rendered HTML (Summary 등) 은 archive html 과 비교 의미가 없어 스킵.
        if (
            newsletter_type == "daily"
            and plan.mode == "normal"
            and "prerendered_html" not in context
        ):
            prev_archive = NewsletterArchiveRepository.get_latest_before(
                session, tenant_id, newsletter_type, date.today()
            )
            if prev_archive and _html_fingerprint(prev_archive.html_content) == _html_fingerprint(html_content):
                plan = alert_plan(plan, "duplicate_content_alert")
                log_prefix = f"{log_prefix}[duplicate_alert]"
                logger.warning(
                    f"{log_prefix} 직전 발송({prev_archive.sent_date})과 컨텐츠 동일 — "
                    f"일반 구독자 발송 중단, SUPER_ADMIN_EMAILS 에만 DUPLICATE 배너 발송"
                )
                send_ops_alert(
                    "duplicate-content 경보 — 구독자 발송 중단",
                    f"직전 발송({prev_archive.sent_date})과 콘텐츠 동일.",
                    tenant_id=tenant_id,
                )
                context["duplicate_alert"] = {
                    "previous_sent_date": prev_archive.sent_date.isoformat(),
                }
                subject = f"[⚠️DUPLICATE] {subject}"
                # 배너 반영을 위해 재렌더링
                try:
                    html_content = renderer.render(template_name, context)
                except Exception as e:
                    logger.error(f"{log_prefix} 템플릿 재렌더링 실패: {e}")
                    return

        # 아카이브 저장 — plan 이 허용하는 경우만 (정식 발송)
        if plan.archive:
            try:
                NewsletterArchiveRepository.save(
                    session, tenant_id, newsletter_type, subject, html_content
                )
                logger.info(f"{log_prefix} 아카이브 저장 완료")
            except Exception as e:
                logger.warning(f"{log_prefix} 아카이브 저장 실패 (발송은 계속): {e}")

        # 구독자 조회 — admin_only(휴일 테스트·경보)는 SUPER_ADMIN_EMAILS 만
        if plan.admin_only:
            subscribers = _get_admin_recipients(session, tenant_id)
            if not subscribers:
                logger.warning(
                    f"{log_prefix} SUPER_ADMIN_EMAILS 가 비어 있어 발송 스킵"
                )
                return
        elif slot:
            subscribers = SubscriberRepository.get_active_by_slot(session, tenant_id, slot)
        else:
            subscribers = SubscriberRepository.get_all_active(session, tenant_id)

        if not subscribers:
            logger.warning(f"[{tenant_id}] 등록된 구독자가 없습니다.")
            return

        # 중복 방지 — plan 이 dedup 을 요구하는 경우만 (정식 발송)
        sent_ids: set[int] = set()
        if plan.dedup:
            if newsletter_type == "daily":
                sent_ids = SendHistoryRepository.get_sent_today_subscriber_ids(
                    session, tenant_id, newsletter_type="daily"
                )
            else:
                period_start = _get_period_start_for_dedup(newsletter_type)
                sent_ids = SendHistoryRepository.get_sent_subscriber_ids_for_period(
                    session, tenant_id, newsletter_type, period_start
                )

        # 발송 대상 메시지 리스트 구성.
        # ORM 객체는 세션 종료 후 만료되므로 (id, email) 튜플로만 보관한다.
        messages = []
        target_recipients: list[tuple[int, str]] = []
        for subscriber in subscribers:
            if subscriber.id in sent_ids:
                logger.debug(f"{log_prefix} 이미 발송됨: {subscriber.email}")
                continue

            messages.append({
                "recipient": subscriber.email,
                "subject": subject,
                "html_content": _personalize_html(
                    html_content, tenant_id, subscriber.unsubscribe_token,
                    subscriber_id=subscriber.id,
                ),
                "sender_name": tenant.display_name,
            })
            target_recipients.append((subscriber.id, subscriber.email))

        if not messages:
            logger.info(f"{log_prefix} 발송 대상이 없습니다 (모두 발송 완료).")
            update_health("send")
            return
        # ── 준비 트랜잭션 종료(아카이브 커밋). 이후 SMTP 발송은 세션 밖
        # (_deliver_and_record 참조 — 비가역 발송을 가역 트랜잭션에 넣지 않는다).

    # 배치 발송 + 행 단위 이력 커밋 + 1회 재시도 (모든 발송 경로 공통 헬퍼)
    sent_count, _failed = _deliver_and_record(
        tenant_id, subject, messages, target_recipients,
        history_type=plan.history_type, send_mode=plan.send_mode,
        log_prefix=log_prefix,
    )

    # dedup: 발송 성공 기사 이력 기록 — plan 이 허용하는 정식 daily 발송만.
    # 경보/테스트 발송은 캐시된 과거 기사를 admin 에게 재발송한 것이므로
    # sent_articles 풀을 오염시키면 안 됨 (다음날 정상 발송 시 잘못 dedup 될 위험).
    if (
        sent_count >= 1
        and plan.record_articles
        and newsletter_type == "daily"
        and tenant.dedup_recent_days
    ):
        try:
            entries = tenant.extract_sent_article_entries(context)
            if entries:
                with get_session() as session:
                    inserted = SentArticleRepository.record_sent_articles(
                        session, tenant_id, date.today(), entries
                    )
                logger.info(
                    f"{log_prefix} sent_articles 기록: {inserted}/{len(entries)}건"
                )
        except Exception as e:
            logger.warning(f"{log_prefix} sent_articles 기록 실패: {e}")

    logger.info(f"{log_prefix} 뉴스레터 발송 완료: {sent_count}/{len(messages)}건")
    update_health("send")


def _prepare_daily_send(session, tenant_id, tenant, type_label):
    """daily 발송용 데이터 준비.

    Returns:
        (context, template_name, subject, max_cache_age_seconds, oldest_collected_at)
        - max_cache_age_seconds: daily 대상 데이터 중 가장 오래된 캐시의 경과 초.
          stale 가드(P6) 분기 판단에 사용. 데이터 없으면 None.
        - oldest_collected_at: 가장 오래된 collected_at (UTC datetime). STALE 배너
          메타데이터에 사용. None 가능.
        실패 시 (None, None, None, None, None).
    """
    collected_with_time = CollectedDataRepository.get_all_latest_with_time(session, tenant_id)

    if not collected_with_time:
        logger.warning(f"[{tenant_id}] 발송할 수집 데이터가 없습니다.")
        return None, None, None, None, None

    # 캐시 데이터 staleness 검사 (24시간 기준 경고). 가장 오래된 캐시 age 도 같이 산출.
    now = datetime.utcnow()
    collected_data = {}
    max_cache_age_seconds: Optional[float] = None
    oldest_collected_at: Optional[datetime] = None
    for data_type, (data_dict, collected_at) in collected_with_time.items():
        # weekly/monthly prefixed 데이터 제외
        if data_type.startswith("weekly_") or data_type.startswith("monthly_"):
            continue
        collected_data[data_type] = data_dict
        if collected_at:
            age = (now - collected_at).total_seconds()
            if max_cache_age_seconds is None or age > max_cache_age_seconds:
                max_cache_age_seconds = age
                oldest_collected_at = collected_at
            if age > 24 * 3600:
                logger.warning(
                    f"[{tenant_id}] '{data_type}' 데이터가 24시간 이상 경과 "
                    f"(수집 시각: {collected_at.isoformat()}). 캐시 데이터로 발송합니다."
                )

    try:
        context = tenant.format_report(collected_data)
    except Exception as e:
        logger.error(f"[{tenant_id}] 데이터 포매팅 실패: {e}")
        return None, None, None, None, None

    template_name = tenant.get_email_template("daily")
    subject = tenant.generate_subject(newsletter_type="daily")
    return context, template_name, subject, max_cache_age_seconds, oldest_collected_at


def _prepare_summary_send(session, tenant_id, tenant, newsletter_type, type_label):
    """weekly/monthly 발송용 데이터 준비"""
    date_from, date_to = _get_period_range(newsletter_type)

    # 이력 데이터 조회
    history_data = CollectedDataRepository.get_history_range(
        session, tenant_id, date_from, date_to
    )

    # 추가 수집된 요약 데이터 (캐시에서)
    collected_with_time = CollectedDataRepository.get_all_latest_with_time(session, tenant_id)
    summary_data = {}
    prefix = f"{newsletter_type}_"
    for data_type, (data_dict, _) in collected_with_time.items():
        if data_type.startswith(prefix):
            original_type = data_type[len(prefix):]
            summary_data[original_type] = data_dict

    # Phase 3: weekly 에서 직전 주 7일치를 _prev_history 로 함께 전달
    # → formatter 가 Week-over-Week Δ + 자동 코멘트 계산에 사용
    if newsletter_type == "weekly":
        period_days = (date_to - date_from).days + 1
        prev_date_to = date_from - timedelta(days=1)
        prev_date_from = prev_date_to - timedelta(days=period_days - 1)
        prev_history = CollectedDataRepository.get_history_range(
            session, tenant_id, prev_date_from, prev_date_to
        )
        if prev_history:
            summary_data["_prev_history"] = prev_history
            logger.info(
                "[%s][weekly] 전주 비교 데이터 %d건 로드 (%s ~ %s)",
                tenant_id, len(prev_history), prev_date_from, prev_date_to,
            )
        else:
            logger.info(
                "[%s][weekly] 전주 비교 데이터 없음 (%s ~ %s) → Δ 미산출",
                tenant_id, prev_date_from, prev_date_to,
            )

    if not history_data and not summary_data:
        logger.warning(f"[{tenant_id}]{type_label} 발송할 이력/요약 데이터가 없습니다.")
        return None, None, None

    try:
        context = tenant.format_summary_report(newsletter_type, history_data, summary_data)
    except Exception as e:
        logger.error(f"[{tenant_id}]{type_label} 요약 데이터 포매팅 실패: {e}")
        return None, None, None

    if not context:
        logger.warning(f"[{tenant_id}]{type_label} 포매팅 결과가 비어있습니다.")
        return None, None, None

    template_name = tenant.get_email_template(newsletter_type)
    subject = tenant.generate_subject(newsletter_type=newsletter_type)
    return context, template_name, subject


def send_welcome_newsletter(tenant_id: str, email: str) -> bool:
    """신규 구독자에게 최신 뉴스레터 즉시 발송

    수집된 데이터가 없으면 건너뛴다.
    발송 성공 시 send_history에 기록하여 당일 중복 발송을 방지한다.
    """
    logger.info(f"[{tenant_id}] 웰컴 뉴스레터 발송: {email}")

    registry = get_registry()
    tenant = registry.get(tenant_id)
    if not tenant:
        logger.error(f"[{tenant_id}] 테넌트를 찾을 수 없습니다.")
        return False

    sender = get_sender()
    if not sender.is_configured:
        logger.warning(f"[{tenant_id}] Gmail 설정이 완료되지 않아 웰컴 발송을 건너뜁니다.")
        return False

    renderer = get_renderer()

    try:
        with get_session() as session:
            subscriber = SubscriberRepository.get_active_by_email(session, tenant_id, email)
            if not subscriber:
                logger.warning(f"[{tenant_id}] 구독자를 찾을 수 없습니다: {email}")
                return False

            if SendHistoryRepository.already_sent_today(
                session, tenant_id, subscriber.id, newsletter_type="daily"
            ):
                logger.info(f"[{tenant_id}] 이미 오늘 발송됨, 웰컴 건너뜀: {email}")
                return True

            # daily 발송 경로(_prepare_daily_send)와 동일하게 weekly/monthly
            # prefixed 캐시는 제외 — 웰컴이 daily 포매터에 요약 데이터를
            # 잘못 주입하던 드리프트 수정.
            collected_data = {
                data_type: data
                for data_type, data in CollectedDataRepository.get_all_latest(
                    session, tenant_id
                ).items()
                if not data_type.startswith(("weekly_", "monthly_"))
            }
            if not collected_data:
                logger.info(f"[{tenant_id}] 수집 데이터 없음, 웰컴 발송 건너뜀: {email}")
                return False

            context = tenant.format_report(collected_data)
            html_content = renderer.render(tenant.email_template, context)
            html_content = _personalize_html(
                html_content, tenant_id, subscriber.unsubscribe_token,
                subscriber_id=subscriber.id,
            )

            subject = tenant.generate_subject()

            result = sender.send(
                recipient=subscriber.email,
                subject=subject,
                html_content=html_content,
                sender_name=tenant.display_name
            )

            SendHistoryRepository.create(
                session, tenant_id, subscriber.id,
                subject, result.success, result.error_message
            )

            if result.success:
                logger.info(f"[{tenant_id}] 웰컴 뉴스레터 발송 성공: {email}")
            else:
                logger.error(f"[{tenant_id}] 웰컴 뉴스레터 발송 실패: {email} - {result.error_message}")

            return result.success

    except Exception as e:
        logger.exception(f"[{tenant_id}] 웰컴 뉴스레터 발송 중 오류: {e}")
        return False


def run_adhoc_send(
    tenant_id: str,
    subject: str,
    html_content: str,
    subscriber_ids: list[int] | None = None,
) -> dict:
    """긴급/이벤트성 뉴스레터 즉시 발송 (adhoc)

    - daily/weekly/monthly 중복 체크와 완전히 분리
    - 같은 날 동일 제목의 adhoc은 중복 방지
    - subscriber_ids가 None이면 전체 활성 구독자에게 발송

    Returns:
        {"total": int, "success": int, "failed": int, "skipped": int}
    """
    logger.info(f"[{tenant_id}][adhoc] 긴급/이벤트 뉴스레터 발송 시작: {subject}")

    sender = get_sender()
    if not sender.is_configured:
        logger.warning(f"[{tenant_id}][adhoc] Gmail 설정이 완료되지 않아 발송을 건너뜁니다.")
        return {"total": 0, "success": 0, "failed": 0, "skipped": 0}

    result = {"total": 0, "success": 0, "failed": 0, "skipped": 0}

    with get_session() as session:
        # 아카이브 저장
        try:
            NewsletterArchiveRepository.save(
                session, tenant_id, "adhoc", subject, html_content
            )
            logger.info(f"[{tenant_id}][adhoc] 아카이브 저장 완료")
        except Exception as e:
            logger.warning(f"[{tenant_id}][adhoc] 아카이브 저장 실패 (발송은 계속): {e}")

        # 구독자 조회
        if subscriber_ids:
            subscribers = [
                SubscriberRepository.get_by_id(session, sid)
                for sid in subscriber_ids
            ]
            subscribers = [s for s in subscribers if s and s.is_active]
        else:
            subscribers = SubscriberRepository.get_all_active(session, tenant_id)

        if not subscribers:
            logger.warning(f"[{tenant_id}][adhoc] 발송 대상 구독자가 없습니다.")
            return result

        result["total"] = len(subscribers)

        # adhoc 중복 방지: 오늘 같은 제목의 adhoc 으로 이미 발송된 구독자만 스킵.
        # (제목 미포함 시 같은 날 두 번째 adhoc 이 첫 발송 수신자 전원을 잘못 스킵)
        sent_ids = SendHistoryRepository.get_sent_today_subscriber_ids(
            session, tenant_id, newsletter_type="adhoc", subject=subject
        )

        registry = get_registry()
        tenant = registry.get(tenant_id)
        display_name = tenant.display_name if tenant else tenant_id

        # ORM 객체는 세션 종료 후 만료되므로 (id, email) 튜플로만 보관
        messages = []
        target_recipients: list[tuple[int, str]] = []
        for subscriber in subscribers:
            if subscriber.id in sent_ids:
                logger.debug(f"[{tenant_id}][adhoc] 이미 발송됨: {subscriber.email}")
                result["skipped"] += 1
                continue

            messages.append({
                "recipient": subscriber.email,
                "subject": subject,
                "html_content": _personalize_html(
                    html_content, tenant_id, subscriber.unsubscribe_token,
                    subscriber_id=subscriber.id,
                ),
                "sender_name": display_name,
            })
            target_recipients.append((subscriber.id, subscriber.email))

        if not messages:
            logger.info(f"[{tenant_id}][adhoc] 발송 대상이 없습니다 (모두 발송 완료).")
            return result
        # ── 준비 트랜잭션 종료(아카이브 커밋). SMTP 는 세션 밖 공통 헬퍼에서.

    sent_count, failed_count = _deliver_and_record(
        tenant_id, subject, messages, target_recipients,
        history_type="adhoc", send_mode="normal",
        log_prefix=f"[{tenant_id}][adhoc]",
    )
    result["success"] = sent_count
    result["failed"] = failed_count

    logger.info(
        f"[{tenant_id}][adhoc] 발송 완료: "
        f"성공 {result['success']}/{result['total']}건, "
        f"실패 {result['failed']}건, 스킵 {result['skipped']}건"
    )
    return result


def run_insight_brief_job(tenant_id: str) -> None:
    """진단키트 부서용 1페이지 인사이트 브리프 발송 (Phase 6).

    config/tenants.yaml 의 insight_brief.enabled=true 일 때 cron 으로 호출.
    수신자는 환경변수 ALLERGY_INSIGHT_BRIEF_RECIPIENTS (콤마 구분, 미설정시 skip).

    데이터 경로:
        collected_data_history (84일치)
        → WeeklyInsightAggregator (버킷·매트릭스·anomaly·entity·data_quality)
        → weekly_insight.html 렌더 → Gmail 발송
    """
    import os
    from datetime import datetime as _dt
    from ..database.repository import get_session_factory
    from ...tenant.allergy_insight.insight_aggregator import (
        WeeklyInsightAggregator,
        load_insight_brief_config,
    )

    logger.info("[%s] insight_brief 발송 작업 시작", tenant_id)

    ib_config = load_insight_brief_config(tenant_id)
    if not ib_config.get("enabled"):
        logger.info("[%s] insight_brief enabled=false → 작업 종료", tenant_id)
        return

    recipients_env = os.getenv("ALLERGY_INSIGHT_BRIEF_RECIPIENTS", "").strip()
    recipients = [r.strip() for r in recipients_env.split(",") if r.strip()]
    if not recipients:
        logger.warning(
            "[%s] insight_brief 수신자 미설정 "
            "(ALLERGY_INSIGHT_BRIEF_RECIPIENTS) → 발송 건너뜀",
            tenant_id,
        )
        return

    weeks = int(ib_config.get("lookback_weeks", 12))
    watch_list = ib_config.get("watch_list") or {}
    watch_counts = {
        "keywords": len(watch_list.get("keywords", [])),
        "companies": len(watch_list.get("companies", [])),
        "entities": len(watch_list.get("entities", [])),
    }

    session_factory = get_session_factory()
    with session_factory() as session:
        date_to = date.today()
        date_from = date_to - timedelta(days=weeks * 7 - 1)
        history_data = CollectedDataRepository.get_history_range(
            session, tenant_id, date_from, date_to
        )

    if not history_data:
        logger.warning(
            "[%s] insight_brief: %d주 누적 데이터 없음 → 발송 건너뜀",
            tenant_id, weeks,
        )
        return

    agg = WeeklyInsightAggregator(watch_list=watch_list)
    buckets = agg.aggregate_weekly_buckets(history_data, weeks=weeks)
    keyword_matrix = agg.compute_keyword_matrix(
        buckets, watch_list.get("keywords", [])
    )
    summary_metrics = agg.compute_summary_metrics(buckets)
    anomalies = agg.detect_anomalies(buckets, keyword_matrix=keyword_matrix)
    entity_trends = agg.extract_entity_trends(
        buckets, watch_companies=watch_list.get("companies", [])
    )
    data_quality = agg.compute_data_quality(buckets, history_data)
    headline = agg.generate_headline(anomalies, keyword_matrix)
    agenda_candidates = agg.render_agenda_candidates(anomalies, keyword_matrix)

    context = {
        "headline": headline,
        "summary_metrics": summary_metrics,
        "keyword_matrix": keyword_matrix,
        "anomalies": anomalies,
        "entity_trends": entity_trends,
        "agenda_candidates": agenda_candidates,
        "data_quality": data_quality,
        "watch_counts": watch_counts,
        "generated_at": _dt.now(),
    }

    renderer = get_renderer()
    html_content = renderer.render(
        "allergy_insight/weekly_insight.html", context
    )

    sender = get_sender()
    if not sender.is_configured:
        logger.warning("[%s] Gmail 미설정 → insight_brief 발송 건너뜀", tenant_id)
        return

    period_label = (
        f"{summary_metrics.get('period_start')} ~ "
        f"{summary_metrics.get('period_end')}"
    )
    subject = (
        f"[AllergyInsight] 진단키트 주간 인사이트 브리프 ({period_label})"
    )

    success_count = 0
    for recipient in recipients:
        try:
            result = sender.send(
                recipient=recipient,
                subject=subject,
                html_content=html_content,
                sender_name="AllergyInsight Insight Brief",
            )
            if result.success:
                success_count += 1
            else:
                logger.error(
                    "[%s] insight_brief 발송 실패 %s: %s",
                    tenant_id, recipient, result.error_message,
                )
        except Exception as exc:
            logger.exception(
                "[%s] insight_brief 발송 중 오류 %s: %s",
                tenant_id, recipient, exc,
            )

    logger.info(
        "[%s] insight_brief 발송 완료: %d/%d 성공",
        tenant_id, success_count, len(recipients),
    )


def run_engagement_relay_job(tenant_id: str = "allergy-insight") -> None:
    """Engagement 이벤트 일 배치 릴레이 + 보존 정책 (P2 — 페르소나 스펙 N4).

    미릴레이(open/click/feedback) 이벤트를 persona_code 스냅샷과 함께
    AllergyInsight `/api/newsletter/engagement` 로 배치 전송한다.
    - 페르소나 키 미설정 → 릴레이 스킵 (로컬 수집은 계속 — 트랙 E 입력)
    - 백엔드 미구현(404)·오류 → 미마킹 유지, 다음 실행에서 재시도
    - 90일 초과 이벤트는 전 테넌트 purge (sent_articles 와 동일 정책)
    """
    import httpx

    from ..database.repo_engagement import EngagementEventRepository
    from ...tenant.allergy_insight.config import (
        tenant_settings as allergy_settings,
    )

    with get_session() as session:
        purged = EngagementEventRepository.purge_older_than(session, days=90)
    if purged:
        logger.info(f"engagement 이벤트 {purged}건 purge (90일 초과)")

    key = allergy_settings.allergy_insight_newsletter_api_key
    if not key:
        logger.info(f"[{tenant_id}] engagement 릴레이 스킵 — 페르소나 키 미설정")
        return

    # 세션 종료 후 ORM 만료 대비 — payload/ids 를 세션 안에서 추출
    with get_session() as session:
        events = EngagementEventRepository.list_unrelayed(
            session, tenant_id, limit=500
        )
        event_ids = [e.id for e in events]
        payload = [
            {
                "subscriber_id": e.subscriber_id,
                "event_type": e.event_type,
                "newsletter_type": e.newsletter_type,
                "persona_code": e.persona_code,
                "target_url": e.target_url,
                "section": e.section,
                "feedback_value": e.feedback_value,
                "occurred_at": e.created_at.isoformat() + "Z",
            }
            for e in events
        ]

    if not payload:
        logger.info(f"[{tenant_id}] engagement 릴레이 — 신규 이벤트 없음")
        return

    url = (
        allergy_settings.allergy_insight_api_url.rstrip("/")
        + "/api/newsletter/engagement"
    )
    try:
        resp = httpx.post(
            url, json={"events": payload},
            headers={"X-Newsletter-Key": key},
            timeout=15.0, trust_env=False,
        )
        if resp.status_code == 200:
            with get_session() as session:
                EngagementEventRepository.mark_relayed(session, event_ids)
            logger.info(
                f"[{tenant_id}] engagement 릴레이 완료: {len(event_ids)}건"
            )
        else:
            # 404 = AllergyInsight 측 N4 수신부 미구현 — 재시도 대기
            logger.warning(
                f"[{tenant_id}] engagement 릴레이 실패 (HTTP {resp.status_code}) "
                f"— {len(event_ids)}건 유지, 다음 실행에서 재시도"
            )
    except Exception as e:
        logger.warning(f"[{tenant_id}] engagement 릴레이 오류: {e}")


def register_all_jobs(scheduler: BlockingScheduler) -> None:
    """TenantRegistry 순회하며 모든 작업 등록.

    구조:
      - 수집: 테넌트당 daily/weekly/monthly 각 1회 (모든 슬롯이 동일 캐시 공유 → 콘텐츠 일관성)
      - 발송: 슬롯(early/mid/late)마다 별도 cron 잡으로 분리
      - weekly/monthly 발송 시간은 daily 슬롯에서 -10분 (slots.WEEKLY_MONTHLY_OFFSET_MINUTES)
    """
    registry = get_registry()

    for tenant in registry.get_all():
        tid = tenant.tenant_id

        # 주기별 스펙: (freq, collect 트리거 kwargs, send 트리거 추가 kwargs, 로그용 day prefix)
        # 세 주기 모두 동일한 "수집 1회 + 슬롯별 발송" 패턴이므로 하나의 루프로 등록.
        # daily 도 weekly/monthly 처럼 supported_frequencies 로 게이트 —
        # weekly 전용 테넌트(StandUp)에 영구 no-op daily 잡 4개가 등록되던 문제 수정.
        freq_specs: list[tuple[str, dict, dict, str]] = []

        if "daily" in tenant.supported_frequencies:
            dc = tenant.schedule_config
            freq_specs.append((
                "daily",
                {"hour": dc["collect_hour"], "minute": dc["collect_minute"]},
                {},
                "",
            ))

        if "weekly" in tenant.supported_frequencies:
            wc = tenant.weekly_schedule_config
            if wc:
                dow = wc.get("day_of_week", "mon")
                freq_specs.append((
                    "weekly",
                    {"day_of_week": dow,
                     "hour": wc.get("collect_hour", 5),
                     "minute": wc.get("collect_minute", 0)},
                    {"day_of_week": dow},
                    f"{dow} ",
                ))

        if "monthly" in tenant.supported_frequencies:
            mc = tenant.monthly_schedule_config
            if mc:
                dom = mc.get("day_of_month", 1)
                day_display = "말일" if str(dom) == "last" else f"{dom}일"
                freq_specs.append((
                    "monthly",
                    {"day": dom,
                     "hour": mc.get("collect_hour", 5),
                     "minute": mc.get("collect_minute", 0)},
                    {"day": dom},
                    f"매월 {day_display} ",
                ))

        for freq, collect_trigger, send_extra, day_prefix in freq_specs:
            # 기존 job id 체계 유지: daily 는 무접미, weekly/monthly 는 _{freq}
            suffix = "" if freq == "daily" else f"_{freq}"
            name_prefix = "" if freq == "daily" else f"{freq.capitalize()} "

            scheduler.add_job(
                run_collect_job,
                trigger=CronTrigger(**collect_trigger),
                args=[tid, freq],
                id=f"collect{suffix}_{tid}",
                name=f"Collect {name_prefix}{tenant.display_name}",
            )
            logger.info(
                f"[{tid}] {freq} 수집 등록: {day_prefix}"
                f"{collect_trigger['hour']:02d}:{collect_trigger['minute']:02d}"
            )

            for s in DAILY_SLOTS:
                s_hour, s_minute = get_slot_time(s["key"], freq)
                scheduler.add_job(
                    run_send_job,
                    trigger=CronTrigger(
                        hour=s_hour, minute=s_minute, **send_extra
                    ),
                    kwargs={"tenant_id": tid, "newsletter_type": freq,
                            "manual": False, "slot": s["key"]},
                    id=f"send{suffix}_{tid}_{s['key']}",
                    name=f"Send {name_prefix}{tenant.display_name} [{s['label']}]",
                )
                logger.info(
                    f"[{tid}] {freq} 발송 등록 [{s['key']}]: "
                    f"{day_prefix}{s_hour:02d}:{s_minute:02d}"
                )

    # === AllergyInsight 진단키트 부서 인사이트 브리프 (Phase 6) ===
    # config/tenants.yaml 의 insight_brief.enabled=true 일 때만 cron 등록.
    # 수신자: 환경변수 ALLERGY_INSIGHT_BRIEF_RECIPIENTS (콤마 구분).
    try:
        from ...tenant.allergy_insight.insight_aggregator import (
            load_insight_brief_config,
        )
        ib_config = load_insight_brief_config("allergy-insight")
        if ib_config.get("enabled"):
            sch = ib_config.get("schedule") or {}
            scheduler.add_job(
                run_insight_brief_job,
                trigger=CronTrigger(
                    day_of_week=sch.get("weekly_day_of_week", "mon"),
                    hour=sch.get("weekly_send_hour", 7),
                    minute=sch.get("weekly_send_minute", 0),
                ),
                args=["allergy-insight"],
                id="insight_brief_allergy_insight",
                name="AllergyInsight 진단키트 부서 인사이트 브리프",
            )
            logger.info(
                "[allergy-insight] insight_brief 등록: %s %02d:%02d (lookback=%d주)",
                sch.get("weekly_day_of_week", "mon"),
                sch.get("weekly_send_hour", 7),
                sch.get("weekly_send_minute", 0),
                ib_config.get("lookback_weeks", 12),
            )
        else:
            logger.info(
                "[allergy-insight] insight_brief enabled=false → cron 등록 건너뜀"
            )
    except Exception as e:
        logger.warning("[allergy-insight] insight_brief 등록 중 예외: %s", e)

    # === Bounce Feedback Loop (30분 주기) ===
    # Gmail inbox에서 NDR 자동 수집 → hard bounce 주소 비활성화 + 재발송 차단
    scheduler.add_job(
        run_bounce_processor,
        trigger=CronTrigger(minute="*/30"),
        id="bounce_processor",
        name="Bounce Feedback Loop (NDR 처리)",
    )
    logger.info("bounce_processor 등록: 30분 주기")

    # === Engagement 릴레이 (일 1회 04:50 — 수집 시작 전) ===
    # 로컬 수집된 open/click/feedback 을 AllergyInsight 로 배치 전송 (N4)
    # + 90일 보존 purge. 페르소나 키 미설정 시 릴레이만 스킵.
    scheduler.add_job(
        run_engagement_relay_job,
        trigger=CronTrigger(hour=4, minute=50),
        id="engagement_relay",
        name="Engagement 이벤트 릴레이 (N4)",
    )
    logger.info("engagement_relay 등록: 매일 04:50")

    # === Heartbeat (10분 주기) ===
    # Docker healthcheck(check_heartbeat)가 스케줄러 루프 생존을 판정하는 근거.
    # 등록 시점에 1회 즉시 기록해 기동 직후 stale 판정을 방지한다.
    update_health("heartbeat")
    scheduler.add_job(
        lambda: update_health("heartbeat"),
        trigger=CronTrigger(minute="*/10"),
        id="scheduler_heartbeat",
        name="Scheduler Heartbeat",
    )
    logger.info("scheduler_heartbeat 등록: 10분 주기")
