"""
스케줄러 헬스체크 모듈

작업 완료 시 헬스 파일 업데이트, Docker 헬스체크에서 검증
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

HEALTH_FILE = Path(__file__).parent.parent.parent.parent / "data" / ".scheduler_health"


def update_health(job_type: str) -> None:
    """작업 완료 시 헬스 파일 업데이트"""
    data = {}
    if HEALTH_FILE.exists():
        try:
            data = json.loads(HEALTH_FILE.read_text())
        except Exception:
            pass

    data[job_type] = datetime.utcnow().isoformat()
    HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_FILE.write_text(json.dumps(data))


def check_heartbeat(max_age_seconds: int = 1800) -> bool:
    """Docker healthcheck 용: 스케줄러 프로세스 생존 판정.

    10분 주기 heartbeat 잡(jobs.register_all_jobs)이 갱신한 타임스탬프가
    max_age_seconds(기본 30분) 이내인지 확인한다. check_health()의 6시간
    잡 실행 창과 달리 발송 없는 시간대에도 오탐하지 않는다.
    """
    if not HEALTH_FILE.exists():
        return True  # 기동 직후 — 파일 생성 전
    try:
        data = json.loads(HEALTH_FILE.read_text())
        hb = data.get("heartbeat")
        if not hb:
            return True  # heartbeat 도입 이전 파일
        last = datetime.fromisoformat(hb)
        return (datetime.utcnow() - last).total_seconds() < max_age_seconds
    except Exception:
        return False


def check_health() -> bool:
    """헬스 체크: 최근 6시간 이내 작업 실행 여부"""
    if not HEALTH_FILE.exists():
        return True  # 시작 직후에는 아직 파일 없으므로 healthy

    try:
        data = json.loads(HEALTH_FILE.read_text())
        for last_run in data.values():
            last = datetime.fromisoformat(last_run)
            if (datetime.utcnow() - last).total_seconds() < 6 * 3600:
                return True
        return False
    except Exception:
        return False
