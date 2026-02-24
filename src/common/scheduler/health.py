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
