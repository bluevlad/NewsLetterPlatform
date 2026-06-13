"""LLMOps batch-run 보고 클라이언트 (BATCH_RUN_REPORTING v0.3.0).

원칙 (표준 §3 불변):
- Fire-and-forget — LLMOps 가 죽어도 뉴스레터 로직은 절대 영향받지 않는다.
- 타임아웃 ≤ 1초, 재시도 없음, 예외 전파 없음 (관측 데이터일 뿐).
- content(prompt/response) 는 §2-β 샘플링: 실패/저품질/1-in-N 만, truncate 후.

의존성 없음 (urllib + threading). 다른 서비스로 1파일 복사 가능.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import urllib.request
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def build_stage_content(
    stage: Dict[str, Any],
    *,
    prompt: Optional[str] = None,
    response: Optional[str] = None,
    ok: bool = True,
    quality_score: Optional[float] = None,
    success_sample_rate: float = 0.1,
    quality_threshold: float = 0.6,
    max_chars: int = 4000,
) -> Dict[str, Any]:
    """실패/저품질/1-in-N 일 때만 prompt/response 본문을 stage 에 truncate 후 첨부.

    본문 미캡처여도 prompt_chars/response_chars(원본 길이)는 항상 채운다.
    """
    capture = (
        (not ok)
        or (quality_score is not None and quality_score < quality_threshold)
        or (random.random() < success_sample_rate)
    )
    stage["content_sampled"] = capture
    for key, text in (("prompt", prompt), ("response", response)):
        if text is None:
            continue
        stage[f"{key}_chars"] = len(text)
        if capture:
            stage[key] = text[:max_chars]
            if len(text) > max_chars:
                stage["content_truncated"] = True
    return stage


def report_batch_run(
    url: str, api_key: str, consumer_id: str, payload: Dict[str, Any]
) -> None:
    """배치 실행 결과를 LLMOps 로 비동기 전송. 절대 예외 throw 하지 않음."""
    if not url or not api_key:
        return  # 미설정 = 보고 비활성

    def _send() -> None:
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-LLMOps-Key": api_key,
                    "X-Consumer-Id": consumer_id,
                },
                method="POST",
            )
            urllib.request.urlopen(req, timeout=1.0).read()
        except Exception as e:  # noqa: BLE001 — 의도적 swallow
            logger.debug("LLMOps 보고 실패(무시): %s", e)

    threading.Thread(target=_send, daemon=True).start()
