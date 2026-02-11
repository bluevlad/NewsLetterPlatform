"""
스케줄러 작업 정의
테넌트별 데이터 수집 및 뉴스레터 발송
"""

import asyncio
import logging
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from ..database.repository import (
    get_session, CollectedDataRepository,
    SubscriberRepository, SendHistoryRepository
)
from ..delivery.gmail_sender import get_sender
from ..template.renderer import get_renderer
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
            logger.warning(f"[{tenant_id}] 수집된 데이터가 없습니다.")
            return

        with get_session() as session:
            for data_type, data in collected.items():
                CollectedDataRepository.upsert(session, tenant_id, data_type, data)

        logger.info(f"[{tenant_id}] 데이터 수집 완료: {list(collected.keys())}")

    except Exception as e:
        logger.exception(f"[{tenant_id}] 데이터 수집 중 오류: {e}")


def run_send_job(tenant_id: str) -> None:
    """뉴스레터 발송 작업"""
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
    sent_count = 0

    with get_session() as session:
        # 캐시된 수집 데이터 로드
        collected_data = CollectedDataRepository.get_all_latest(session, tenant_id)

        if not collected_data:
            logger.warning(f"[{tenant_id}] 발송할 수집 데이터가 없습니다.")
            return

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

        # 구독자 조회 및 발송
        subscribers = SubscriberRepository.get_all_active(session, tenant_id)

        if not subscribers:
            logger.warning(f"[{tenant_id}] 등록된 구독자가 없습니다.")
            return

        for subscriber in subscribers:
            try:
                if SendHistoryRepository.already_sent_today(session, tenant_id, subscriber.id):
                    logger.debug(f"[{tenant_id}] 이미 발송됨: {subscriber.email}")
                    continue

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
                    sent_count += 1
                    logger.info(f"[{tenant_id}] 발송 성공: {subscriber.email}")
                else:
                    logger.error(f"[{tenant_id}] 발송 실패: {subscriber.email} - {result.error_message}")

            except Exception as e:
                logger.error(f"[{tenant_id}] 발송 중 오류 ({subscriber.email}): {e}")

    logger.info(f"[{tenant_id}] 뉴스레터 발송 완료: {sent_count}건")


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
