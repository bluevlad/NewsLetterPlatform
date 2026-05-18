"""TechBriefing — 운영 서비스 Service Profile 로더.

`config/service_profiles/*.yaml` 을 1회 로드 후 캐시.
스코어러가 외부 기술 정보를 운영 서비스(예: hopenvision) 관점에서
relevance 평가할 때 사용.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# 프로젝트 루트 / config / service_profiles
_PROFILE_DIR = Path(__file__).resolve().parents[3] / "config" / "service_profiles"


@dataclass(frozen=True)
class KnownDebt:
    area: str
    state: str = ""
    note: Optional[str] = None
    priority: Optional[str] = None


@dataclass(frozen=True)
class SignalWeights:
    per_high_interest: float = 2.0
    per_low_interest: float = 3.0
    per_known_debt: float = 1.5
    high_cap: float = 4.0
    debt_cap: float = 3.0


@dataclass(frozen=True)
class ServiceProfile:
    service: str
    purpose: str = ""
    stack_summary: str = ""              # LLM 프롬프트 주입용 한 줄 요약
    high_interest: tuple[str, ...] = ()
    low_interest: tuple[str, ...] = ()
    known_debt: tuple[KnownDebt, ...] = ()
    watching: tuple[str, ...] = ()
    weights: SignalWeights = field(default_factory=SignalWeights)

    @property
    def has_signals(self) -> bool:
        return bool(self.high_interest or self.low_interest or self.known_debt)


_cache: Optional[list[ServiceProfile]] = None


def _parse(raw: dict) -> ServiceProfile:
    service = (raw.get("service") or "").strip()
    if not service:
        raise ValueError("service_profile yaml: 'service' 필드 필수")

    rel = raw.get("relevance_signals") or {}
    ctx = raw.get("context") or {}

    high = tuple(s for s in (rel.get("high_interest") or []) if isinstance(s, str) and s.strip())
    low = tuple(s for s in (rel.get("low_interest") or []) if isinstance(s, str) and s.strip())

    debt_items: list[KnownDebt] = []
    for d in ctx.get("known_debt") or []:
        if not isinstance(d, dict):
            continue
        area = (d.get("area") or "").strip()
        if not area:
            continue
        debt_items.append(KnownDebt(
            area=area,
            state=(d.get("state") or "").strip(),
            note=d.get("note"),
            priority=d.get("priority"),
        ))

    watching = tuple(s for s in (ctx.get("watching") or []) if isinstance(s, str) and s.strip())

    w_raw = raw.get("signal_weights") or {}
    weights = SignalWeights(
        per_high_interest=float(w_raw.get("per_high_interest", 2.0)),
        per_low_interest=float(w_raw.get("per_low_interest", 3.0)),
        per_known_debt=float(w_raw.get("per_known_debt", 1.5)),
        high_cap=float(w_raw.get("high_cap", 4.0)),
        debt_cap=float(w_raw.get("debt_cap", 3.0)),
    )

    return ServiceProfile(
        service=service,
        purpose=(raw.get("purpose") or "").strip(),
        stack_summary=(raw.get("stack_summary") or "").strip(),
        high_interest=high,
        low_interest=low,
        known_debt=tuple(debt_items),
        watching=watching,
        weights=weights,
    )


def load_profiles(*, force_reload: bool = False) -> list[ServiceProfile]:
    """`config/service_profiles/*.yaml` 모두 로드 (캐시).

    파일이 한 개도 없거나 디렉토리 자체가 없으면 빈 리스트 반환 — 시스템은
    기존 importance_score 만으로 정상 동작.
    """
    global _cache
    if _cache is not None and not force_reload:
        return _cache

    profiles: list[ServiceProfile] = []
    if not _PROFILE_DIR.exists():
        logger.info(f"service_profiles 디렉토리 없음 — relevance 비활성: {_PROFILE_DIR}")
        _cache = profiles
        return profiles

    for path in sorted(_PROFILE_DIR.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            profiles.append(_parse(raw))
            logger.info(f"service_profile 로드: {path.name}")
        except Exception as e:
            logger.warning(f"service_profile 로드 실패 [{path.name}]: {e}")

    _cache = profiles
    return profiles


def reset_cache() -> None:
    """테스트용 — 캐시 강제 초기화."""
    global _cache
    _cache = None
