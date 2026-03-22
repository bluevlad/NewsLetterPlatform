"""
스케줄러 작업 정의
테넌트별 데이터 수집 및 뉴스레터 발송
"""

import asyncio
import logging
import time
from datetime import datetime, date, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from ..database.repository import (
    get_session, CollectedDataRepository,
    SubscriberRepository, SendHistoryRepository,
    NewsletterArchiveRepository
)
from ..delivery.gmail_sender import get_sender
from ..template.renderer import get_renderer
from .health import update_health
from ...config import settings
from ...tenant.registry import get_registry

logger = logging.getLogger(__name__)


def _get_period_range(newsletter_type: str) -> tuple[date, date]:
    """뉴스레터 유형별 집계 기간 계산

    Returns:
        (date_from, date_to) - 집계 시작일, 종료일
    """
    today = date.today()
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
    KST = timezone(timedelta(hours=9))
    now_kst = datetime.now(KST)
    today = now_kst.date()

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
            collected = asyncio.run(tenant.collect_data())
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

    update_health("collect")


def run_send_job(
    tenant_id: str, newsletter_type: str = "daily", manual: bool = False
) -> None:
    """뉴스레터 발송 작업 (배치 발송 + 실패 재시도)

    Args:
        manual: True이면 수동 발송 모드 — dedup 스킵, newsletter_type="manual"로 이력 저장.
                자동 스케줄 발송 이력(daily/weekly/monthly)에 영향을 주지 않는다.
    """
    # 이력 저장용 타입: manual이면 "manual", 아니면 원래 newsletter_type
    history_type = "manual" if manual else newsletter_type
    mode_label = "[manual]" if manual else ""
    type_label = f"[{newsletter_type}]" if newsletter_type != "daily" else ""
    log_prefix = f"[{tenant_id}]{mode_label}{type_label}"
    logger.info(f"{log_prefix} 뉴스레터 발송 시작")

    registry = get_registry()
    tenant = registry.get(tenant_id)
    if not tenant:
        logger.error(f"[{tenant_id}] 테넌트를 찾을 수 없습니다.")
        return

    sender = get_sender()
    if not sender.is_configured:
        logger.warning(f"[{tenant_id}] Gmail 설정이 완료되지 않아 발송을 건너뜁니다.")
        return

    renderer = get_renderer()

    with get_session() as session:
        if newsletter_type == "daily":
            # 기존 daily 로직
            context, template_name, subject = _prepare_daily_send(
                session, tenant_id, tenant, type_label
            )
        else:
            # weekly/monthly 로직
            context, template_name, subject = _prepare_summary_send(
                session, tenant_id, tenant, newsletter_type, type_label
            )

        if context is None:
            return

        # HTML 렌더링
        try:
            html_content = renderer.render(template_name, context)
        except Exception as e:
            logger.error(f"{log_prefix} 템플릿 렌더링 실패: {e}")
            return

        # 아카이브 저장 (수동 발송은 아카이브 생략 — 동일 날짜 중복 방지)
        if not manual:
            try:
                NewsletterArchiveRepository.save(
                    session, tenant_id, newsletter_type, subject, html_content
                )
                logger.info(f"{log_prefix} 아카이브 저장 완료")
            except Exception as e:
                logger.warning(f"{log_prefix} 아카이브 저장 실패 (발송은 계속): {e}")

        # 구독자 조회
        subscribers = SubscriberRepository.get_all_active(session, tenant_id)

        if not subscribers:
            logger.warning(f"[{tenant_id}] 등록된 구독자가 없습니다.")
            return

        # 중복 방지: 수동 발송은 dedup 스킵
        sent_ids: set[int] = set()
        if not manual:
            if newsletter_type == "daily":
                sent_ids = SendHistoryRepository.get_sent_today_subscriber_ids(
                    session, tenant_id, newsletter_type="daily"
                )
            else:
                period_start = _get_period_start_for_dedup(newsletter_type)
                sent_ids = SendHistoryRepository.get_sent_subscriber_ids_for_period(
                    session, tenant_id, newsletter_type, period_start
                )

        # 발송 대상 메시지 리스트 구성
        messages = []
        target_subscribers = []
        for subscriber in subscribers:
            if subscriber.id in sent_ids:
                logger.debug(f"{log_prefix} 이미 발송됨: {subscriber.email}")
                continue

            unsubscribe_url = (
                f"{settings.web_base_url}/{tenant_id}"
                f"/unsubscribe/token/{subscriber.unsubscribe_token}"
            )
            subscriber_html = html_content.replace("__UNSUBSCRIBE_URL__", unsubscribe_url)

            messages.append({
                "recipient": subscriber.email,
                "subject": subject,
                "html_content": subscriber_html,
                "sender_name": tenant.display_name,
            })
            target_subscribers.append(subscriber)

        if not messages:
            logger.info(f"{log_prefix} 발송 대상이 없습니다 (모두 발송 완료).")
            update_health("send")
            return

        # 1차 배치 발송
        results = sender.send_batch_efficient(messages)

        # 1차 결과 기록 (history_type으로 저장하여 자동/수동 이력 분리)
        failed_items = []
        sent_count = 0
        for subscriber, msg, result in zip(target_subscribers, messages, results):
            SendHistoryRepository.create(
                session, tenant_id, subscriber.id,
                subject, result.success, result.error_message,
                newsletter_type=history_type
            )
            if result.success:
                sent_count += 1
            else:
                failed_items.append((subscriber, msg))
                logger.error(f"{log_prefix} 발송 실패: {subscriber.email} - {result.error_message}")

        # 2차 재시도 (실패 건)
        if failed_items:
            logger.info(f"{log_prefix} {len(failed_items)}건 재시도 (5초 후)")
            time.sleep(5)

            retry_messages = [msg for _, msg in failed_items]
            retry_results = sender.send_batch_efficient(retry_messages)

            for (subscriber, _), retry_result in zip(failed_items, retry_results):
                if retry_result.success:
                    SendHistoryRepository.create(
                        session, tenant_id, subscriber.id,
                        subject, True, None,
                        newsletter_type=history_type
                    )
                    sent_count += 1
                    logger.info(f"{log_prefix} 재시도 발송 성공: {subscriber.email}")
                else:
                    logger.error(
                        f"{log_prefix} 재시도 발송 실패: {subscriber.email} - {retry_result.error_message}"
                    )

    logger.info(f"{log_prefix} 뉴스레터 발송 완료: {sent_count}/{len(messages)}건")
    update_health("send")


def _prepare_daily_send(session, tenant_id, tenant, type_label):
    """daily 발송용 데이터 준비"""
    collected_with_time = CollectedDataRepository.get_all_latest_with_time(session, tenant_id)

    if not collected_with_time:
        logger.warning(f"[{tenant_id}] 발송할 수집 데이터가 없습니다.")
        return None, None, None

    # 캐시 데이터 staleness 검사 (24시간 기준)
    now = datetime.utcnow()
    collected_data = {}
    for data_type, (data_dict, collected_at) in collected_with_time.items():
        # weekly/monthly prefixed 데이터 제외
        if data_type.startswith("weekly_") or data_type.startswith("monthly_"):
            continue
        collected_data[data_type] = data_dict
        if collected_at and (now - collected_at).total_seconds() > 24 * 3600:
            logger.warning(
                f"[{tenant_id}] '{data_type}' 데이터가 24시간 이상 경과 "
                f"(수집 시각: {collected_at.isoformat()}). 캐시 데이터로 발송합니다."
            )

    try:
        context = tenant.format_report(collected_data)
    except Exception as e:
        logger.error(f"[{tenant_id}] 데이터 포매팅 실패: {e}")
        return None, None, None

    template_name = tenant.get_email_template("daily")
    subject = tenant.generate_subject(newsletter_type="daily")
    return context, template_name, subject


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

            collected_data = CollectedDataRepository.get_all_latest(session, tenant_id)
            if not collected_data:
                logger.info(f"[{tenant_id}] 수집 데이터 없음, 웰컴 발송 건너뜀: {email}")
                return False

            context = tenant.format_report(collected_data)
            html_content = renderer.render(tenant.email_template, context)

            unsubscribe_url = (
                f"{settings.web_base_url}/{tenant_id}"
                f"/unsubscribe/token/{subscriber.unsubscribe_token}"
            )
            html_content = html_content.replace("__UNSUBSCRIBE_URL__", unsubscribe_url)

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

        # adhoc 중복 방지: 오늘 같은 adhoc 타입으로 이미 발송된 구독자
        sent_ids = SendHistoryRepository.get_sent_today_subscriber_ids(
            session, tenant_id, newsletter_type="adhoc"
        )

        registry = get_registry()
        tenant = registry.get(tenant_id)
        display_name = tenant.display_name if tenant else tenant_id

        messages = []
        target_subscribers = []
        for subscriber in subscribers:
            if subscriber.id in sent_ids:
                logger.debug(f"[{tenant_id}][adhoc] 이미 발송됨: {subscriber.email}")
                result["skipped"] += 1
                continue

            unsubscribe_url = (
                f"{settings.web_base_url}/{tenant_id}"
                f"/unsubscribe/token/{subscriber.unsubscribe_token}"
            )
            subscriber_html = html_content.replace("__UNSUBSCRIBE_URL__", unsubscribe_url)

            messages.append({
                "recipient": subscriber.email,
                "subject": subject,
                "html_content": subscriber_html,
                "sender_name": display_name,
            })
            target_subscribers.append(subscriber)

        if not messages:
            logger.info(f"[{tenant_id}][adhoc] 발송 대상이 없습니다 (모두 발송 완료).")
            return result

        # 배치 발송
        send_results = sender.send_batch_efficient(messages)

        for subscriber, send_result in zip(target_subscribers, send_results):
            SendHistoryRepository.create(
                session, tenant_id, subscriber.id,
                subject, send_result.success, send_result.error_message,
                newsletter_type="adhoc"
            )
            if send_result.success:
                result["success"] += 1
            else:
                result["failed"] += 1
                logger.error(
                    f"[{tenant_id}][adhoc] 발송 실패: {subscriber.email} - {send_result.error_message}"
                )

    logger.info(
        f"[{tenant_id}][adhoc] 발송 완료: "
        f"성공 {result['success']}/{result['total']}건, "
        f"실패 {result['failed']}건, 스킵 {result['skipped']}건"
    )
    return result


def register_all_jobs(scheduler: BlockingScheduler) -> None:
    """TenantRegistry 순회하며 모든 작업 등록"""
    registry = get_registry()

    for tenant in registry.get_all():
        config = tenant.schedule_config
        tid = tenant.tenant_id

        # Daily 스케줄 등록
        scheduler.add_job(
            run_collect_job,
            trigger=CronTrigger(
                hour=config["collect_hour"],
                minute=config["collect_minute"]
            ),
            args=[tid, "daily"],
            id=f"collect_{tid}",
            name=f"Collect {tenant.display_name}",
        )

        scheduler.add_job(
            run_send_job,
            trigger=CronTrigger(
                hour=config["send_hour"],
                minute=config["send_minute"]
            ),
            args=[tid, "daily"],
            id=f"send_{tid}",
            name=f"Send {tenant.display_name}",
        )

        logger.info(
            f"[{tid}] daily 스케줄 등록: "
            f"수집 {config['collect_hour']:02d}:{config['collect_minute']:02d}, "
            f"발송 {config['send_hour']:02d}:{config['send_minute']:02d}"
        )

        # Weekly 스케줄 등록
        if "weekly" in tenant.supported_frequencies:
            wc = tenant.weekly_schedule_config
            if wc:
                scheduler.add_job(
                    run_collect_job,
                    trigger=CronTrigger(
                        day_of_week=wc.get("day_of_week", "mon"),
                        hour=wc.get("collect_hour", 7),
                        minute=wc.get("collect_minute", 0),
                    ),
                    args=[tid, "weekly"],
                    id=f"collect_weekly_{tid}",
                    name=f"Collect Weekly {tenant.display_name}",
                )
                scheduler.add_job(
                    run_send_job,
                    trigger=CronTrigger(
                        day_of_week=wc.get("day_of_week", "mon"),
                        hour=wc.get("send_hour", 9),
                        minute=wc.get("send_minute", 0),
                    ),
                    args=[tid, "weekly"],
                    id=f"send_weekly_{tid}",
                    name=f"Send Weekly {tenant.display_name}",
                )
                logger.info(
                    f"[{tid}] weekly 스케줄 등록: "
                    f"{wc.get('day_of_week', 'mon')} "
                    f"수집 {wc.get('collect_hour', 7):02d}:{wc.get('collect_minute', 0):02d}, "
                    f"발송 {wc.get('send_hour', 9):02d}:{wc.get('send_minute', 0):02d}"
                )

        # Monthly 스케줄 등록
        if "monthly" in tenant.supported_frequencies:
            mc = tenant.monthly_schedule_config
            if mc:
                scheduler.add_job(
                    run_collect_job,
                    trigger=CronTrigger(
                        day=mc.get("day_of_month", 1),
                        hour=mc.get("collect_hour", 7),
                        minute=mc.get("collect_minute", 0),
                    ),
                    args=[tid, "monthly"],
                    id=f"collect_monthly_{tid}",
                    name=f"Collect Monthly {tenant.display_name}",
                )
                scheduler.add_job(
                    run_send_job,
                    trigger=CronTrigger(
                        day=mc.get("day_of_month", 1),
                        hour=mc.get("send_hour", 10),
                        minute=mc.get("send_minute", 0),
                    ),
                    args=[tid, "monthly"],
                    id=f"send_monthly_{tid}",
                    name=f"Send Monthly {tenant.display_name}",
                )
                day_label = mc.get('day_of_month', 1)
                day_display = "말일" if str(day_label) == "last" else f"{day_label}일"
                logger.info(
                    f"[{tid}] monthly 스케줄 등록: "
                    f"매월 {day_display} "
                    f"수집 {mc.get('collect_hour', 7):02d}:{mc.get('collect_minute', 0):02d}, "
                    f"발송 {mc.get('send_hour', 10):02d}:{mc.get('send_minute', 0):02d}"
                )
