"""
스케줄러 작업 정의
테넌트별 데이터 수집 및 뉴스레터 발송
"""

import asyncio
import logging
import time
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from ..database.repository import (
    get_session, CollectedDataRepository,
    SubscriberRepository, SendHistoryRepository
)
from ..delivery.gmail_sender import get_sender
from ..template.renderer import get_renderer
from .health import update_health
from ...config import settings
from ...tenant.registry import get_registry

logger = logging.getLogger(__name__)


def run_collect_job(tenant_id: str) -> None:
    """데이터 수집 작업"""
    logger.info(f"[{tenant_id}] 데이터 수집 시작")

    registry = get_registry()
    tenant = registry.get(tenant_id)
    if not tenant:
        logger.error(f"[{tenant_id}] 테넌트를 찾을 수 없습니다.")
        return

    try:
        collected = asyncio.run(tenant.collect_data())

        if not collected:
            logger.warning(
                f"[{tenant_id}] 수집된 데이터가 없습니다. "
                "이전 캐시 데이터로 발송됩니다."
            )
            update_health("collect")
            return

        with get_session() as session:
            for data_type, data in collected.items():
                CollectedDataRepository.upsert(session, tenant_id, data_type, data)

        logger.info(f"[{tenant_id}] 데이터 수집 완료: {list(collected.keys())}")

    except Exception as e:
        logger.exception(
            f"[{tenant_id}] 데이터 수집 중 오류: {e}. "
            "이전 캐시 데이터로 발송됩니다."
        )

    update_health("collect")


def run_send_job(tenant_id: str) -> None:
    """뉴스레터 발송 작업 (배치 발송 + 실패 재시도)"""
    logger.info(f"[{tenant_id}] 뉴스레터 발송 시작")

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
        # 캐시된 수집 데이터 로드 (수집 시각 포함)
        collected_with_time = CollectedDataRepository.get_all_latest_with_time(session, tenant_id)

        if not collected_with_time:
            logger.warning(f"[{tenant_id}] 발송할 수집 데이터가 없습니다.")
            return

        # 캐시 데이터 staleness 검사 (24시간 기준)
        now = datetime.utcnow()
        collected_data = {}
        for data_type, (data_dict, collected_at) in collected_with_time.items():
            collected_data[data_type] = data_dict
            if collected_at and (now - collected_at).total_seconds() > 24 * 3600:
                logger.warning(
                    f"[{tenant_id}] '{data_type}' 데이터가 24시간 이상 경과 "
                    f"(수집 시각: {collected_at.isoformat()}). 캐시 데이터로 발송합니다."
                )

        # 데이터 포매팅
        try:
            context = tenant.format_report(collected_data)
        except Exception as e:
            logger.error(f"[{tenant_id}] 데이터 포매팅 실패: {e}")
            return

        # HTML 렌더링
        try:
            html_content = renderer.render(tenant.email_template, context)
        except Exception as e:
            logger.error(f"[{tenant_id}] 템플릿 렌더링 실패: {e}")
            return

        # 이메일 제목
        subject = tenant.generate_subject()

        # 구독자 조회
        subscribers = SubscriberRepository.get_all_active(session, tenant_id)

        if not subscribers:
            logger.warning(f"[{tenant_id}] 등록된 구독자가 없습니다.")
            return

        # 당일 발송 완료된 구독자 ID 일괄 조회 (N+1 쿼리 방지)
        sent_today_ids = SendHistoryRepository.get_sent_today_subscriber_ids(session, tenant_id)

        # 발송 대상 메시지 리스트 구성
        messages = []
        target_subscribers = []
        for subscriber in subscribers:
            if subscriber.id in sent_today_ids:
                logger.debug(f"[{tenant_id}] 이미 발송됨: {subscriber.email}")
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
            logger.info(f"[{tenant_id}] 발송 대상이 없습니다 (모두 발송 완료).")
            update_health("send")
            return

        # 1차 배치 발송
        results = sender.send_batch_efficient(messages)

        # 1차 결과 기록
        failed_items = []
        sent_count = 0
        for subscriber, msg, result in zip(target_subscribers, messages, results):
            SendHistoryRepository.create(
                session, tenant_id, subscriber.id,
                subject, result.success, result.error_message
            )
            if result.success:
                sent_count += 1
            else:
                failed_items.append((subscriber, msg))
                logger.error(f"[{tenant_id}] 발송 실패: {subscriber.email} - {result.error_message}")

        # 2차 재시도 (실패 건)
        if failed_items:
            logger.info(f"[{tenant_id}] {len(failed_items)}건 재시도 (5초 후)")
            time.sleep(5)

            retry_messages = [msg for _, msg in failed_items]
            retry_results = sender.send_batch_efficient(retry_messages)

            for (subscriber, _), retry_result in zip(failed_items, retry_results):
                if retry_result.success:
                    SendHistoryRepository.create(
                        session, tenant_id, subscriber.id,
                        subject, True, None
                    )
                    sent_count += 1
                    logger.info(f"[{tenant_id}] 재시도 발송 성공: {subscriber.email}")
                else:
                    logger.error(
                        f"[{tenant_id}] 재시도 발송 실패: {subscriber.email} - {retry_result.error_message}"
                    )

    logger.info(f"[{tenant_id}] 뉴스레터 발송 완료: {sent_count}/{len(messages)}건")
    update_health("send")


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

            if SendHistoryRepository.already_sent_today(session, tenant_id, subscriber.id):
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


def register_all_jobs(scheduler: BlockingScheduler) -> None:
    """TenantRegistry 순회하며 모든 작업 등록"""
    registry = get_registry()

    for tenant in registry.get_all():
        config = tenant.schedule_config
        tid = tenant.tenant_id

        scheduler.add_job(
            run_collect_job,
            trigger=CronTrigger(
                hour=config["collect_hour"],
                minute=config["collect_minute"]
            ),
            args=[tid],
            id=f"collect_{tid}",
            name=f"Collect {tenant.display_name}",
        )

        scheduler.add_job(
            run_send_job,
            trigger=CronTrigger(
                hour=config["send_hour"],
                minute=config["send_minute"]
            ),
            args=[tid],
            id=f"send_{tid}",
            name=f"Send {tenant.display_name}",
        )

        logger.info(
            f"[{tid}] 스케줄 등록: "
            f"수집 {config['collect_hour']:02d}:{config['collect_minute']:02d}, "
            f"발송 {config['send_hour']:02d}:{config['send_minute']:02d}"
        )
