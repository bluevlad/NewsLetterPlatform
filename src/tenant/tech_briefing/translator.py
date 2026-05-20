"""TechBriefing 헤드라인 번역기 — Today's N 카드의 영문 제목/요약을 한글화.

analyzer 와 독립 — Service Profile 매칭 여부와 무관하게 모든 헤드라인을 커버한다.
효율을 위해 헤드라인 전체를 하나의 프롬프트로 묶어 Ollama 를 1회만 호출한다.

LLM 비활성 / 호출 실패 / 파싱 실패 시 noop → 템플릿이 영문으로 graceful fallback.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from ...config import settings
from .ollama_client import chat, parse_json_array_response
from .prompts import TRANSLATE_SYSTEM, render_translate_prompt

logger = logging.getLogger(__name__)


def translate_headlines(items: List[Dict[str, Any]]) -> int:
    """헤드라인 리스트를 in-place 로 enrich — item['title_ko'], item['summary_ko'].

    - 번역 비활성 → noop, 0 반환
    - 빈 리스트 → noop, 0 반환
    - LLM 호출/파싱 실패 → noop (item 미변경, 템플릿이 영문 fallback)

    Returns: 제목 번역에 성공한 아이템 수.
    """
    if not settings.tech_briefing_translate_enabled:
        logger.info("TechBriefing 번역 비활성 — translator skip")
        return 0
    if not items:
        return 0

    user_prompt = render_translate_prompt(items)

    try:
        result = chat(system=TRANSLATE_SYSTEM, user=user_prompt, max_tokens=2000)
        if not result.ok:
            logger.warning("번역 LLM 호출 실패 — 영문 fallback: %s", result.error)
            return 0

        parsed = parse_json_array_response(result.text)
        if not parsed:
            logger.warning(
                "번역 응답 파싱 실패 — 영문 fallback (excerpt: %s)",
                (result.text or "")[:120],
            )
            return 0
    except Exception as e:
        logger.exception("translate_headlines 예외 — 영문 fallback: %s", e)
        return 0

    # i(1-based) → 번역 결과 매핑.
    by_index: Dict[int, Dict[str, Any]] = {}
    for entry in parsed:
        if not isinstance(entry, dict) or "i" not in entry:
            continue
        try:
            by_index[int(entry["i"])] = entry
        except (TypeError, ValueError):
            continue

    success = 0
    for idx, item in enumerate(items, 1):
        entry = by_index.get(idx)
        if not entry:
            continue
        title_ko = (entry.get("title_ko") or "").strip()
        summary_ko = (entry.get("summary_ko") or "").strip()
        if title_ko:
            item["title_ko"] = title_ko
            success += 1
        if summary_ko:
            item["summary_ko"] = summary_ko

    logger.info(
        "TechBriefing 번역: %d/%d 헤드라인 한글화 (eval_ms=%d)",
        success, len(items), result.eval_duration_ms,
    )
    return success
