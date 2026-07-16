"""TechBriefing Deep Analyzer — top N 헤드라인을 Ollama 로 4섹션 JSON 분석.

각 카드 호출 독립 try/except. 1건 실패해도 다른 카드는 표시.
LLM 실패 시 item['analysis'] = None — 템플릿에서 자동으로 summary fallback.

교육·커리어 도메인 전환 후에는 Service Profile 없이 모든 헤드라인을
독자(취준생/주니어/시니어) 프로필 기준으로 분석한다.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from ...common.llmops_client import build_stage_content, report_batch_run
from ...config import settings
from .ollama_client import chat, parse_json_response
from .prompts import ANALYZE_SYSTEM, render_user_prompt

logger = logging.getLogger(__name__)

# 권장 등급 화이트리스트 — LLM 환각 방어.
_LEVEL_WHITELIST = {"APPLY", "PLAN", "WATCH", "SKIP"}


def _normalize_analysis(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """LLM JSON 응답을 안전 스키마로 정규화. 핵심 필드 누락 시 None."""
    if not isinstance(raw, dict):
        return None

    what = (raw.get("what_it_is") or "").strip()
    who = (raw.get("who_benefits") or "").strip()
    tip = (raw.get("action_tip") or "").strip()

    rec_raw = raw.get("recommendation") or {}
    if not isinstance(rec_raw, dict):
        rec_raw = {}
    level = (rec_raw.get("level") or "").strip().upper()
    if level not in _LEVEL_WHITELIST:
        level = "WATCH"   # 등급 이상 시 안전한 중립값
    rationale = (rec_raw.get("rationale") or "").strip()

    # what_it_is 라도 있어야 카드 의미 있음 — 비면 fallback (LLM 호출 자체가 망가진 셈)
    if not what:
        return None

    return {
        "what_it_is":   what,
        "who_benefits": who,
        "recommendation": {"level": level, "rationale": rationale},
        "action_tip":   tip,
    }


def analyze_headlines(items: List[Dict[str, Any]]) -> int:
    """헤드라인 리스트를 in-place 로 enrich — item['analysis'] 부여.

    - LLM 비활성 → noop, 0 반환
    - LLM 호출 실패/파싱 실패 → item['analysis'] = None (템플릿이 fallback)

    Returns: 분석 성공한 아이템 수.
    """
    if not settings.tech_briefing_llm_enabled:
        logger.info("TechBriefing LLM 비활성 — analyzer skip")
        return 0

    top_n = settings.tech_briefing_llm_top_n
    candidates = items[:top_n]
    success = 0

    # LLMOps 관측 — 호출 단위 trace 수집 (fire-and-forget, §2-β content 샘플링)
    started_at = datetime.now(timezone.utc)
    stages: List[Dict[str, Any]] = []

    for idx, item in enumerate(candidates, 1):
        user_prompt = render_user_prompt(item=item)

        try:
            result = chat(system=ANALYZE_SYSTEM, user=user_prompt, max_tokens=900)
            # 호출 메트릭 — tokens_in 은 ollama 가 미노출 → 생략(추정 금지)
            stage: Dict[str, Any] = {
                "name": "analyze",
                "model": result.model,
                "tokens_out": result.eval_count or None,
                "duration_ms": result.eval_duration_ms or None,
                "params": {
                    "temperature": settings.tech_briefing_llm_temperature,
                    "max_tokens": 900,
                },
            }

            if not result.ok:
                logger.warning(
                    "[analyzer #%d] LLM 호출 실패 — fallback: %s",
                    idx, result.error,
                )
                item["analysis"] = None
                stage.update(ok=False, error=result.error)
                # 품질 0 (호출 자체 실패) — heuristic
                stage["quality"] = {"score": 0.0, "judge": "heuristic",
                                    "dimensions": {"json_valid": 0.0}}
                stages.append(build_stage_content(
                    stage, prompt=user_prompt, response=result.text,
                    ok=False, quality_score=0.0,
                    success_sample_rate=0.15,
                ))
                continue

            parsed = parse_json_response(result.text)
            normalized = _normalize_analysis(parsed) if parsed else None
            if not normalized:
                logger.warning(
                    "[analyzer #%d] LLM 응답 파싱/정규화 실패 — fallback (excerpt: %s)",
                    idx, (result.text or "")[:120],
                )
                item["analysis"] = None
                # 호출은 됐으나 JSON 파싱/정규화 실패 = 토큰 낭비 케이스 (효율 핵심 신호)
                stage.update(ok=False, error="JSON parse/normalize failed")
                stage["quality"] = {"score": 0.0, "judge": "heuristic",
                                    "dimensions": {"json_valid": 0.0}}
                stages.append(build_stage_content(
                    stage, prompt=user_prompt, response=result.text,
                    ok=False, quality_score=0.0,
                    success_sample_rate=0.15,
                ))
                continue

            item["analysis"] = normalized
            item["analysis_meta"] = {
                "model":  result.model,
                "eval_ms": result.eval_duration_ms,
            }
            success += 1
            stage.update(ok=True)
            stage["quality"] = {"score": 1.0, "judge": "heuristic",
                                "dimensions": {"json_valid": 1.0}}
            stages.append(build_stage_content(
                stage, prompt=user_prompt, response=result.text,
                ok=True, quality_score=1.0,
                success_sample_rate=0.15,
            ))
            logger.info(
                "[analyzer #%d] OK category=%s level=%s eval_ms=%d",
                idx, item.get("category", ""),
                normalized["recommendation"]["level"], result.eval_duration_ms,
            )
        except Exception as e:
            logger.exception("[analyzer #%d] 예외 — fallback: %s", idx, e)
            item["analysis"] = None
            stages.append({"name": "analyze", "ok": False, "error": str(e),
                           "content_sampled": False})

    _report_to_llmops(started_at, stages, success)
    return success


def _report_to_llmops(
    started_at: datetime, stages: List[Dict[str, Any]], success: int
) -> None:
    """수집한 stage trace 를 LLMOps 로 fire-and-forget 보고. 절대 throw 안 함."""
    if not settings.llmops_enabled or not stages:
        return
    attempted = len(stages)
    if success == attempted:
        run_status = "success"
    elif success == 0:
        run_status = "failure"
    else:
        run_status = "partial"
    payload = {
        "consumer_id": settings.tech_briefing_consumer_id,
        "run_id": f"{started_at.isoformat()}-{uuid.uuid4().hex[:8]}",
        "started_at": started_at.isoformat(),
        "ended_at": datetime.now(timezone.utc).isoformat(),
        "status": run_status,
        "stages": stages,
        "metrics": {"items_analyzed": success, "items_attempted": attempted},
    }
    report_batch_run(
        settings.llmops_url, settings.llmops_api_key,
        settings.tech_briefing_consumer_id, payload,
    )
