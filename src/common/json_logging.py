"""stdout JSON Lines 로그 포맷터 — LogAnalyzer(loganalyzer.unmong.com) 수집 파이프라인 계약.

필드: ts/level/logger/msg (+ exc_info). LOG_JSON=0 이면 사용 안 함.
"""

import json
import logging
import os
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            data["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def use_json_logging() -> bool:
    return os.environ.get("LOG_JSON", "1").lower() not in ("0", "false")
