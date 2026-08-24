"""StandUp Ops Insight 섹션 — collector payload → daily 템플릿 컨텍스트.

StandUp(9065) 주간 합성 결과(LogAnalyzer / GitHub QA / Auto-Tobe)를
TechBriefing daily 의 한 섹션으로 게재한다. 구 standup tenant
(legacy/standup/) 의 formatter 를 섹션 규모로 축약한 것.

payload 계약 (collector.collect_ops_insight):
    {
        "newsletter_id": str, "dedup_id": int,
        "period_start": ISO date, "period_end": ISO date,
        "headline": str | None, "subject": str | None,
        "kpis": {..., "fix_verifications": [...]},
        "events": [...],  # /api/v1/insight/events (보조 — 실패 시 빈 리스트)
    }
"""

import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 심각도 badge 메타 — legacy/standup formatter 와 동일 팔레트.
_SEVERITY_META = {
    "critical": {"label": "Critical", "color": "#b91c1c", "bg": "#fee2e2"},
    "high":     {"label": "High",     "color": "#c2410c", "bg": "#ffedd5"},
    "medium":   {"label": "Medium",   "color": "#a16207", "bg": "#fef9c3"},
    "low":      {"label": "Low",      "color": "#1d4ed8", "bg": "#dbeafe"},
    "info":     {"label": "Info",     "color": "#475569", "bg": "#f1f5f9"},
}
_SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")

_SOURCE_LABEL = {
    "loganalyzer": "LogAnalyzer",
    "github_qa": "GitHub QA",
    "auto_tobe_journal": "Auto-Tobe",
    "auto_tobe_commit": "Auto-Tobe",
    "medium_digest_report": "Tech Trend",
    "tech_news_article": "Tech Trend",
}

# fix 효과 검증 상태 표시 (StandUp fix_verification_service 계약).
_VERIFICATION_META = {
    "verified": {"icon": "✅", "label": "검증됨 (재발 없음)",
                 "color": "#15803d", "bg": "#dcfce7"},
    "recurred": {"icon": "❌", "label": "재발",
                 "color": "#b91c1c", "bg": "#fee2e2"},
    "pending":  {"icon": "⏳", "label": "검증 중",
                 "color": "#a16207", "bg": "#fef9c3"},
    "unlinked": {"icon": "➖", "label": "검증 불가 (fingerprint 미연결)",
                 "color": "#475569", "bg": "#f1f5f9"},
}

TOP_EVENTS_LIMIT = 5
BY_SERVICE_LIMIT = 3
VERIFICATION_LIMIT = 6

# top events 에서 제외 — KPI 메타 이벤트(개별 오류 아님)와 기술 뉴스
# (기술 토픽 큐레이션은 TechBriefing 본 섹션 담당이라 ops 에선 노이즈).
_EXCLUDED_EVENT_CATEGORIES = {"kpi_summary", "error_types"}
_EXCLUDED_EVENT_SOURCES = {"tech_news_article", "medium_digest_report"}


def _date_display(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%m-%d")
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10]).strftime("%m-%d")
        except ValueError:
            return value[:10]
    return "—"


def _severity_meta(severity: Any) -> Dict[str, str]:
    return _SEVERITY_META.get(
        (str(severity or "info")).lower(), _SEVERITY_META["info"]
    )


def _enrich_event(ev: Dict[str, Any]) -> Dict[str, Any]:
    sev = (ev.get("severity") or "info").lower()
    meta = _severity_meta(sev)
    source = ev.get("source_type") or ""
    occurred = ev.get("occurred_at") or ""
    return {
        "title_safe": ev.get("title") or "(제목 없음)",
        "service_tag": ev.get("service_tag") or "",
        "severity": sev,
        "severity_label": meta["label"],
        "severity_color": meta["color"],
        "severity_bg": meta["bg"],
        "source_label": _SOURCE_LABEL.get(source, source or "—"),
        "occurred_display": occurred[5:10] if isinstance(occurred, str) else "—",
    }


def _top_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """심각도 우선 + 최신순 top N. KPI 메타/기술 뉴스 이벤트 제외."""
    events = [
        e for e in events
        if (e.get("category") or "") not in _EXCLUDED_EVENT_CATEGORIES
        and (e.get("source_type") or "") not in _EXCLUDED_EVENT_SOURCES
    ]
    sev_rank = {s: i for i, s in enumerate(_SEVERITY_ORDER)}
    ranked = sorted(events, key=lambda e: e.get("occurred_at") or "", reverse=True)
    ranked = sorted(
        ranked,
        key=lambda e: sev_rank.get((e.get("severity") or "info").lower(), 99),
    )
    return [_enrich_event(e) for e in ranked[:TOP_EVENTS_LIMIT]]


def _kpi_chips(kpis: Dict[str, Any], events_total: int) -> List[Dict[str, Any]]:
    chips: List[Dict[str, Any]] = []
    summary = kpis.get("loganalyzer_summary") or {}
    if summary:
        chips.append({"label": "총 오류", "value": summary.get("total_errors", 0)})
        chips.append({"label": "Critical", "value": summary.get("critical", 0)})
        chips.append({"label": "High", "value": summary.get("high", 0)})
    chips.append({
        "label": "수집 이벤트",
        "value": kpis.get("total_events", events_total),
    })
    return chips


def _by_service(kpis: Dict[str, Any]) -> List[Dict[str, Any]]:
    counts = kpis.get("by_service") or {}
    ranked = sorted(counts.items(), key=lambda x: -int(x[1] or 0))
    return [
        {"name": name, "count": int(count)}
        for name, count in ranked[:BY_SERVICE_LIMIT]
        if count
    ]


def _verifications(kpis: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for v in (kpis.get("fix_verifications") or [])[:VERIFICATION_LIMIT]:
        meta = _VERIFICATION_META.get(
            (v.get("status") or "").lower(), _VERIFICATION_META["unlinked"]
        )
        out.append({
            "title": v.get("fix_title") or "(제목 없음)",
            "status": v.get("status"),
            "icon": meta["icon"],
            "label": meta["label"],
            "color": meta["color"],
            "bg": meta["bg"],
            "fingerprint": v.get("fingerprint") or "",
            "recurrence_count": v.get("recurrence_count") or 0,
        })
    return out


def build_ops_context(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """collector 의 ops_insight payload → 템플릿 컨텍스트. 비면 None."""
    if not payload:
        return None
    kpis: Dict[str, Any] = payload.get("kpis") or {}
    events: List[Dict[str, Any]] = payload.get("events") or []
    return {
        "newsletter_id": payload.get("newsletter_id"),
        "dedup_id": payload.get("dedup_id"),
        "headline": payload.get("headline") or "주간 StandUp Insight 요약",
        "period_display": (
            f"{_date_display(payload.get('period_start'))} ~ "
            f"{_date_display(payload.get('period_end'))}"
        ),
        "kpi_chips": _kpi_chips(kpis, len(events)),
        "by_service": _by_service(kpis),
        "top_events": _top_events(events),
        "verifications": _verifications(kpis),
    }
