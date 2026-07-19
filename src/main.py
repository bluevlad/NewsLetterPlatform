"""
NewsLetterPlatform 메인 실행 파일
멀티테넌트 뉴스레터 통합 플랫폼
"""

import logging
import sys
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import settings
from src.common.database import init_db
from src.common.scheduler.jobs import (
    run_collect_job, run_send_job, register_all_jobs
)
from src.tenant.registry import get_registry
from src.tenant.allergy_insight import AllergyInsightTenant
from src.tenant.standup import StandUpTenant
from src.tenant.tech_briefing import TechBriefingTenant

from src.common.json_logging import JsonFormatter, use_json_logging

# 로깅 설정 — stdout은 JSON(LogAnalyzer 수집용), 파일은 가독 포맷 유지
_plain_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

_stream_handler = logging.StreamHandler(sys.stdout)
_stream_handler.setFormatter(JsonFormatter() if use_json_logging() else _plain_formatter)

_file_handler = logging.FileHandler(
    settings.BASE_DIR / "logs" / "newsletterplatform.log",
    encoding="utf-8"
)
_file_handler.setFormatter(_plain_formatter)

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    handlers=[_stream_handler, _file_handler],
)

logger = logging.getLogger(__name__)


def register_tenants():
    """테넌트 등록 - AllergyInsight + StandUp"""
    registry = get_registry()
    registry.register(AllergyInsightTenant())
    registry.register(StandUpTenant())
    registry.register(TechBriefingTenant())
    logger.info(f"테넌트 등록 완료: {registry.get_active_ids()}")


def run_scheduler():
    """스케줄러 실행"""
    logger.info("NewsLetterPlatform 스케줄러 시작")

    scheduler = BlockingScheduler()
    register_all_jobs(scheduler)

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("스케줄러 종료")
        scheduler.shutdown()


def main():
    """메인 함수"""
    import argparse

    parser = argparse.ArgumentParser(description="NewsLetterPlatform - 멀티테넌트 뉴스레터 플랫폼")
    parser.add_argument("--web", action="store_true", help="웹 서버 실행")
    parser.add_argument("--run-once", action="store_true", help="즉시 한 번 실행 (수집 → 발송)")
    parser.add_argument("--collect-only", action="store_true", help="수집만 실행")
    parser.add_argument("--send-only", action="store_true", help="발송만 실행")
    parser.add_argument("--tenant", type=str, help="특정 테넌트만 실행", default=None)
    parser.add_argument(
        "--newsletter-type", type=str, default="daily",
        choices=["daily", "weekly", "monthly"],
        help="뉴스레터 유형 (daily/weekly/monthly, 기본: daily)"
    )

    args = parser.parse_args()

    # 환경 변수 로드
    load_dotenv()

    # 로그 디렉토리 생성
    (settings.BASE_DIR / "logs").mkdir(exist_ok=True)

    # 데이터베이스 초기화
    logger.info("데이터베이스 초기화...")
    init_db(settings.database_url)

    # 테넌트 등록
    register_tenants()

    # 대상 테넌트 결정
    registry = get_registry()
    if args.tenant:
        tenant = registry.get(args.tenant)
        if not tenant:
            logger.error(f"테넌트를 찾을 수 없습니다: {args.tenant}")
            sys.exit(1)
        target_ids = [args.tenant]
    else:
        target_ids = registry.get_active_ids()

    nl_type = args.newsletter_type

    if args.web:
        logger.info("웹 서버 모드")
        from src.web.app import run_server
        run_server()
    elif args.collect_only:
        logger.info(f"수집만 실행: {target_ids} (type={nl_type})")
        for tid in target_ids:
            run_collect_job(tid, nl_type)
    elif args.send_only:
        logger.info(f"발송만 실행 (manual): {target_ids} (type={nl_type})")
        for tid in target_ids:
            run_send_job(tid, nl_type, manual=True)
    elif args.run_once:
        logger.info(f"즉시 실행 모드 (manual): {target_ids} (type={nl_type})")
        for tid in target_ids:
            run_collect_job(tid, nl_type)
        for tid in target_ids:
            run_send_job(tid, nl_type, manual=True)
    else:
        run_scheduler()


if __name__ == "__main__":
    main()
