"""Ollama HTTP client — TechBriefing analyzer 전용 미니 포팅.

StandUp `app/synthesis/ollama_client.py` 패턴 차용:
- sync /api/chat 호출 (단발)
- trust_env=False — host proxy 환경변수가 Ollama 호출 가로채는 사고 방지
- 실패 시 예외 미발생, GenResult(ok=False) 로 반환 → 카드 단위 graceful fallback

JSON 강제: caller 가 prompt 에 "JSON 외 출력 금지" 명시 + 응답을 json.loads 시도.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from ...config import settings

logger = logging.getLogger(__name__)


@dataclass
class GenResult:
    text: str
    model: str
    eval_count: int
    eval_duration_ms: int
    ok: bool
    error: Optional[str] = None


# 가장 바깥 JSON 객체 추출 — 모델이 prose 를 앞뒤에 붙여도 핵심 JSON 만 파싱.
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def chat(
    system: str,
    user: str,
    *,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: int = 1024,
    timeout: Optional[int] = None,
) -> GenResult:
    """단발 chat. 실패 시 ok=False."""
    base_url = settings.ollama_base_url.rstrip("/")
    url = f"{base_url}/api/chat"
    use_model = model or settings.tech_briefing_llm_model
    use_temp = settings.tech_briefing_llm_temperature if temperature is None else temperature
    use_timeout = timeout or settings.tech_briefing_llm_timeout_sec

    payload: Dict[str, Any] = {
        "model": use_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "stream": False,
        "options": {
            "temperature": use_temp,
            "num_predict": max_tokens,
        },
    }
    try:
        with httpx.Client(timeout=use_timeout, trust_env=False) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
        text = ((data.get("message") or {}).get("content") or "").strip()
        return GenResult(
            text=text, model=use_model,
            eval_count=data.get("eval_count", 0),
            eval_duration_ms=int(data.get("eval_duration", 0) / 1_000_000),
            ok=True,
        )
    except Exception as e:
        logger.warning("ollama chat 실패 model=%s err=%s", use_model, e)
        return GenResult(text="", model=use_model, eval_count=0,
                         eval_duration_ms=0, ok=False, error=str(e))


def parse_json_response(text: str) -> Optional[Dict[str, Any]]:
    """LLM 응답에서 JSON 객체 추출. 실패 시 None."""
    if not text:
        return None
    # 1차: 그대로 시도
    try:
        return json.loads(text)
    except Exception:
        pass
    # 2차: 가장 큰 {...} 블록만 추출 (모델이 prose 를 붙인 경우)
    m = _JSON_OBJ_RE.search(text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    logger.debug("ollama 응답 JSON 파싱 실패 (excerpt): %s", text[:200])
    return None
