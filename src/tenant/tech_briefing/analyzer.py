"""TechBriefing Deep Analyzer — top N 헤드라인을 Ollama 로 4섹션 JSON 분석.

각 카드 호출 독립 try/except. 1건 실패해도 다른 카드는 표시.
LLM 실패 시 item['analysis'] = None — 템플릿에서 자동으로 summary fallback.

운영 서비스 컨텍스트 = Service Profile (config/service_profiles/*.yaml).
프로파일이 없거나 LLM 비활성이면 noop.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ...config import settings
from .ollama_client import chat, parse_json_response
from .prompts import ANALYZE_SYSTEM, render_user_prompt
from .service_profiles import ServiceProfile, load_profiles

logger = logging.getLogger(__name__)

# 권장 등급 화이트리스트 — LLM 환각 방어.
_LEVEL_WHITELIST = {"ADOPT", "TRIAL", "ASSESS", "HOLD"}


def _normalize_analysis(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """LLM JSON 응답을 안전 스키마로 정규화. 핵심 필드 누락 시 None."""
    if not isinstance(raw, dict):
        return None

    what = (raw.get("what_changed") or "").strip()
    impact = (raw.get("service_impact") or "").strip()
    cost = (raw.get("estimated_cost") or "").strip()

    rec_raw = raw.get("recommendation") or {}
    if not isinstance(rec_raw, dict):
        rec_raw = {}
    level = (rec_raw.get("level") or "").strip().upper()
    if level not in _LEVEL_WHITELIST:
        level = "ASSESS"   # 등급 이상 시 안전한 중립값
    rationale = (rec_raw.get("rationale") or "").strip()

    # what_changed 라도 있어야 카드 의미 있음 — 비면 fallback (LLM 호출 자체가 망가진 셈)
    if not what:
        return None

    return {
        "what_changed":  what,
        "service_impact": impact,
        "recommendation": {"level": level, "rationale": rationale},
        "estimated_cost": cost,
    }


def _pick_profile_for_item(
    item: Dict[str, Any], profiles: List[ServiceProfile]
) -> Optional[ServiceProfile]:
    """아이템과 가장 매칭되는 프로파일 1개 선택.

    item 의 service_relevance 가 가장 높은 서비스. 0 이하면 None (분석 불필요).
    """
    rel = item.get("service_relevance") or {}
    if not rel or not profiles:
        return None
    name = max(rel, key=lambda s: rel[s].get("score", 0))
    if rel[name].get("score", 0) <= 0:
        return None
    return next((p for p in profiles if p.service == name), None)


def analyze_headlines(
    items: List[Dict[str, Any]], *, stack_summaries: Optional[Dict[str, str]] = None
) -> int:
    """헤드라인 리스트를 in-place 로 enrich — item['analysis'] 부여.

    - LLM 비활성 → noop, 0 반환
    - 프로파일 미로드 → noop
    - 매칭 프로파일 없는 아이템 → 건너뜀
    - LLM 호출 실패/파싱 실패 → item['analysis'] = None (템플릿이 fallback)

    Returns: 분석 성공한 아이템 수.
    """
    if not settings.tech_briefing_llm_enabled:
        logger.info("TechBriefing LLM 비활성 — analyzer skip")
        return 0

    profiles = load_profiles()
    if not profiles:
        logger.info("Service profile 없음 — analyzer skip")
        return 0

    if stack_summaries is None:
        # 프로파일에 stack_summary 가 yaml 에 있다면 사용. 미설정이면 빈값.
        stack_summaries = {
            p.service: getattr(p, "stack_summary", "") or "" for p in profiles
        }

    top_n = settings.tech_briefing_llm_top_n
    candidates = items[:top_n]
    success = 0

    for idx, item in enumerate(candidates, 1):
        profile = _pick_profile_for_item(item, profiles)
        if not profile:
            item.setdefault("analysis", None)
            continue

        user_prompt = render_user_prompt(
            service=profile.service,
            stack_summary=stack_summaries.get(profile.service, ""),
            high_signals=list(profile.high_interest),
            known_debt=[
                {"area": d.area, "state": d.state} for d in profile.known_debt
            ],
            item=item,
        )

        try:
            result = chat(system=ANALYZE_SYSTEM, user=user_prompt, max_tokens=900)
            if not result.ok:
                logger.warning(
                    "[analyzer #%d] LLM 호출 실패 — fallback: %s",
                    idx, result.error,
                )
                item["analysis"] = None
                continue

            parsed = parse_json_response(result.text)
            normalized = _normalize_analysis(parsed) if parsed else None
            if not normalized:
                logger.warning(
                    "[analyzer #%d] LLM 응답 파싱/정규화 실패 — fallback (excerpt: %s)",
                    idx, (result.text or "")[:120],
                )
                item["analysis"] = None
                continue

            item["analysis"] = normalized
            item["analysis_meta"] = {
                "model":  result.model,
                "eval_ms": result.eval_duration_ms,
                "profile": profile.service,
            }
            success += 1
            logger.info(
                "[analyzer #%d] OK profile=%s level=%s eval_ms=%d",
                idx, profile.service,
                normalized["recommendation"]["level"], result.eval_duration_ms,
            )
        except Exception as e:
            logger.exception("[analyzer #%d] 예외 — fallback: %s", idx, e)
            item["analysis"] = None

    return success
