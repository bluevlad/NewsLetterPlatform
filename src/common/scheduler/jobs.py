"""
스케줄러 작업 정의
테넌트별 데이터 수집 및 뉴스레터 발송
- 지수 백오프 재시도 (최대 3회)
- JobExecution 테이블로 멱등성 보장
- 데이터 보존 정책 (인증 30일, 발송이력 90일)
"""

import asyncio
import logging
import time
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from ..database.repository import (
    get_session, CollectedDataRepository,
    SubscriberRepository, SendHistoryRepository,
    JobExecutionRepository, DataRetentionRepository
)
from ..delivery.gmail_sender import get_sender
from ..template.renderer import get_renderer
from ...config import settings
from ...tenant.registry import get_registry

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF_BASE = 2  # 2초, 4초, 8초


def _retry_with_backoff(func, *args, max_retries=MAX_RETRIES):
    """지수 백오프 재시도 래퍼. 마지막 실패 시 예외를 다시 발생시킨다."""
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            return func(*args)
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                wait = BACKOFF_BASE ** attempt
                logger.warning(
                    "Retry %d/%d for %s(%s) after %ds: %s",
                    attempt, max_retries, func.__name__, args, wait, e
                )
                time.sleep(wait)
            else:
                logger.error(
                    "All %d retries exhausted for %s(%s): %s",
                    max_retries, func.__name__, args, e
                )
    raise last_error


def _do_collect(tenant_id: str) -> None:
    """데이터 수집 핵심 로직 (재시도 대상)"""
    registry = get_registry()
    tenant = registry.get(tenant_id)
    if not tenant:
        raise ValueError(f"테넌트를 찾을 수 없습니다: {tenant_id}")

    collected = asyncio.run(tenant.collect_data())

    if not collected:
        logger.warning("[%s] 수집된 데이터가 없습니다.", tenant_id)
        return

    with get_session() as session:
        for data_type, data in collected.items():
            CollectedDataRepository.upsert(session, tenant_id, data_type, data)

    logger.info("[%s] 데이터 수집 완료: %s", tenant_id, list(collected.keys()))


def run_collect_job(tenant_id: str) -> None:
    """데이터 수집 작업 (멱등성 + 재시도)"""
    logger.info("[%s] 데이터 수집 시작", tenant_id)

    with get_session() as session:
        execution = JobExecutionRepository.start_execution(session, "collect", tenant_id)
        if execution is None:
            logger.info("[%s] 오늘 이미 수집 완료. 건너뜀.", tenant_id)
            return
        execution_id = execution.id

    try:
        _retry_with_backoff(_do_collect, tenant_id)

        with get_session() as session:
            JobExecutionRepository.mark_success(session, execution_id)

    except Exception as e:
        with get_session() as session:
            JobExecutionRepository.mark_failed(session, execution_id, str(e)[:500])
        logger.exception("[%s] 데이터 수집 최종 실패: %s", tenant_id, e)


def _do_send(tenant_id: str) -> None:
    """뉴스레터 발송 핵심 로직 (재시도 대상)"""
    registry = get_registry()
    tenant = registry.get(tenant_id)
    if not tenant:
        raise ValueError(f"테넌트를 찾을 수 없습니다: {tenant_id}")

    sender = get_sender()
    if not sender.is_configured:
        logger.warning("[%s] Gmail 설정이 완료되지 않아 발송을 건너뜁니다.", tenant_id)
        return

    renderer = get_renderer()
    sent_count = 0

    with get_session() as session:
        collected_data = CollectedDataRepository.get_all_latest(session, tenant_id)

        if not collected_data:
            logger.warning("[%s] 발송할 수집 데이터가 없습니다.", tenant_id)
            return

        try:
            context = tenant.format_report(collected_data)
        except Exception as e:
            logger.error("[%s] 데이터 포매팅 실패: %s", tenant_id, e)
            return

        try:
            html_content = renderer.render(tenant.email_template, context)
        except Exception as e:
            logger.error("[%s] 템플릿 렌더링 실패: %s", tenant_id, e)
            return

        subject = tenant.generate_subject()

        subscribers = SubscriberRepository.get_all_active(session, tenant_id)

        if not subscribers:
            logger.warning("[%s] 등록된 구독자가 없습니다.", tenant_id)
            return

        # 당일 발송 완료된 구독자 ID 일괄 조회 (N+1 쿼리 방지)
        sent_today_ids = SendHistoryRepository.get_sent_today_subscriber_ids(session, tenant_id)

        for subscriber in subscribers:
            try:
                if subscriber.id in sent_today_ids:
                    logger.debug("[%s] 이미 발송됨: %s", tenant_id, subscriber.email)
                    continue

                unsubscribe_url = (
                    f"{settings.web_base_url}/{tenant_id}"
                    f"/unsubscribe/token/{subscriber.unsubscribe_token}"
                )
                subscriber_html = html_content.replace("__UNSUBSCRIBE_URL__", unsubscribe_url)

                result = sender.send(
                    recipient=subscriber.email,
                    subject=subject,
                    html_content=subscriber_html,
                    sender_name=tenant.display_name
                )

                SendHistoryRepository.create(
                    session, tenant_id, subscriber.id,
                    subject, result.success, result.error_message
                )

                if result.success:
                    sent_count += 1
                    logger.info("[%s] 발송 성공: %s", tenant_id, subscriber.email)
                else:
                    logger.error("[%s] 발송 실패: %s - %s", tenant_id, subscriber.email, result.error_message)

            except Exception as e:
                logger.error("[%s] 발송 중 오류 (%s): %s", tenant_id, subscriber.email, e)

    logger.info("[%s] 뉴스레터 발송 완료: %d건", tenant_id, sent_count)


def run_send_job(tenant_id: str) -> None:
    """뉴스레터 발송 작업 (멱등성 + 재시도)"""
    logger.info("[%s] 뉴스레터 발송 시작", tenant_id)

    with get_session() as session:
        execution = JobExecutionRepository.start_execution(session, "send", tenant_id)
        if execution is None:
            logger.info("[%s] 오늘 이미 발송 완료. 건너뜀.", tenant_id)
            return
        execution_id = execution.id

    try:
        _retry_with_backoff(_do_send, tenant_id)

        with get_session() as session:
            JobExecutionRepository.mark_success(session, execution_id)

    except Exception as e:
        with get_session() as session:
            JobExecutionRepository.mark_failed(session, execution_id, str(e)[:500])
        logger.exception("[%s] 뉴스레터 발송 최종 실패: %s", tenant_id, e)


def send_welcome_newsletter(tenant_id: str, email: str) -> bool:
    """신규 구독자에게 최신 뉴스레터 즉시 발송

    수집된 데이터가 없으면 건너뛴다.
    발송 성공 시 send_history에 기록하여 당일 중복 발송을 방지한다.
    """
    logger.info("[%s] 웰컴 뉴스레터 발송: %s", tenant_id, email)

    registry = get_registry()
    tenant = registry.get(tenant_id)
    if not tenant:
        logger.error("[%s] 테넌트를 찾을 수 없습니다.", tenant_id)
        return False

    sender = get_sender()
    if not sender.is_configured:
        logger.warning("[%s] Gmail 설정이 완료되지 않아 웰컴 발송을 건너뜁니다.", tenant_id)
        return False

    renderer = get_renderer()

    try:
        with get_session() as session:
            subscriber = SubscriberRepository.get_active_by_email(session, tenant_id, email)
            if not subscriber:
                logger.warning("[%s] 구독자를 찾을 수 없습니다: %s", tenant_id, email)
                return False

            if SendHistoryRepository.already_sent_today(session, tenant_id, subscriber.id):
                logger.info("[%s] 이미 오늘 발송됨, 웰컴 건너뜀: %s", tenant_id, email)
                return True

            collected_data = CollectedDataRepository.get_all_latest(session, tenant_id)
            if not collected_data:
                logger.info("[%s] 수집 데이터 없음, 웰컴 발송 건너뜀: %s", tenant_id, email)
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
                logger.info("[%s] 웰컴 뉴스레터 발송 성공: %s", tenant_id, email)
            else:
                logger.error("[%s] 웰컴 뉴스레터 발송 실패: %s - %s", tenant_id, email, result.error_message)

            return result.success

    except Exception as e:
        logger.exception("[%s] 웰컴 뉴스레터 발송 중 오류: %s", tenant_id, e)
        return False


def run_data_retention_job() -> None:
    """데이터 보존 정책 실행 (매일 새벽 실행)"""
    logger.info("데이터 보존 정책 실행 시작")
    try:
        with get_session() as session:
            v_count = DataRetentionRepository.cleanup_expired_verifications(session, retention_days=30)
            h_count = DataRetentionRepository.cleanup_old_send_history(session, retention_days=90)
            j_count = DataRetentionRepository.cleanup_old_job_executions(session, retention_days=30)
        logger.info(
            "데이터 보존 정책 완료: 인증=%d건, 발송이력=%d건, Job이력=%d건 삭제",
            v_count, h_count, j_count
        )
    except Exception as e:
        logger.exception("데이터 보존 정책 실행 중 오류: %s", e)


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
            "[%s] 스케줄 등록: 수집 %02d:%02d, 발송 %02d:%02d",
            tid, config["collect_hour"], config["collect_minute"],
            config["send_hour"], config["send_minute"]
        )

    # 데이터 보존 정책 Job (매일 03:00 실행)
    scheduler.add_job(
        run_data_retention_job,
        trigger=CronTrigger(hour=3, minute=0),
        id="data_retention",
        name="Data Retention Cleanup",
    )
    logger.info("데이터 보존 정책 Job 등록: 매일 03:00")
