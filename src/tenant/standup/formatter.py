"""StandUp 데이터 포매터 — collector 결과를 weekly 템플릿 컨텍스트로 변환."""

import logging
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


# 심각도 표시 메타 (badge 색상 + 한글 라벨).
_SEVERITY_META = {
    "critical": {"label": "Critical", "color": "#b91c1c", "bg": "#fee2e2"},
    "high":     {"label": "High",     "color": "#c2410c", "bg": "#ffedd5"},
    "medium":   {"label": "Medium",   "color": "#a16207", "bg": "#fef9c3"},
    "low":      {"label": "Low",      "color": "#1d4ed8", "bg": "#dbeafe"},
    "info":     {"label": "Info",     "color": "#475569", "bg": "#f1f5f9"},
}

_SOURCE_LABEL = {
    "loganalyzer": "LogAnalyzer",
    "github_qa":   "GitHub QA",
    "auto_tobe":   "Auto-Tobe",
}

_SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")


def _parse_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except Exception:
            pass
    return date.today()


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            pass
    return datetime.now()


def _severity_meta(severity: str) -> Dict[str, str]:
    return _SEVERITY_META.get(
        (severity or "info").lower(), _SEVERITY_META["info"]
    )


class StandUpFormatter:
    """StandUp Insight 합성 결과 → weekly 이메일 컨텍스트."""

    # 템플릿에 노출할 이벤트 최대 개수 (요약 섹션 가독성 확보).
    MAX_EVENTS_DISPLAY = 30
    TOP_EVENTS_PER_SEVERITY = 5

    def format_weekly(self, collected_data: Dict[str, Any]) -> Dict[str, Any]:
        """weekly_insight payload → 템플릿 컨텍스트.

        Args:
            collected_data: collector 가 반환한 dict. 키는 `weekly_insight`.

        Returns:
            템플릿 변수. 데이터 없으면 빈 dict (스케줄러가 발송 스킵).
        """
        payload = (collected_data or {}).get("weekly_insight") or {}
        if not payload:
            return {}

        period_start = _parse_date(payload.get("period_start"))
        period_end = _parse_date(payload.get("period_end"))
        generated_at = _parse_datetime(payload.get("generated_at"))

        events: List[Dict[str, Any]] = payload.get("events") or []
        kpis: Dict[str, Any] = payload.get("kpis") or {}

        by_severity = self._group_by_severity(events)
        by_service = self._aggregate_kv(kpis.get("by_service") or {}, events,
                                        key="service_tag")
        by_source = self._aggregate_kv(kpis.get("by_source") or {}, events,
                                       key="source_type",
                                       label_map=_SOURCE_LABEL)
        severity_buckets = self._severity_buckets(events, kpis)

        top_events = self._top_events(events)
        events_display = events[: self.MAX_EVENTS_DISPLAY]

        return {
            "report_date": generated_at,
            "period_start": period_start,
            "period_end": period_end,
            "headline": payload.get("headline") or "주간 StandUp Insight 요약",
            "source_subject": payload.get("subject"),
            "source_newsletter_id": payload.get("source_newsletter_id"),
            # 통계 카드
            "stats": {
                "total_events": payload.get("events_total") or len(events),
                "critical_count": severity_buckets.get("critical", 0),
                "high_count": severity_buckets.get("high", 0),
                "medium_count": severity_buckets.get("medium", 0),
                "service_count": len(by_service),
                "source_count": len(by_source),
            },
            # 분포
            "severity_buckets": [
                {
                    "key": s,
                    "label": _SEVERITY_META[s]["label"],
                    "color": _SEVERITY_META[s]["color"],
                    "bg": _SEVERITY_META[s]["bg"],
                    "count": severity_buckets.get(s, 0),
                }
                for s in _SEVERITY_ORDER
                if severity_buckets.get(s, 0) > 0
            ],
            "by_service": by_service,
            "by_source": by_source,
            # 클러스터
            "events_by_severity": [
                {
                    "key": s,
                    "label": _SEVERITY_META[s]["label"],
                    "color": _SEVERITY_META[s]["color"],
                    "bg": _SEVERITY_META[s]["bg"],
                    "events": by_severity.get(s, [])[: self.TOP_EVENTS_PER_SEVERITY],
                    "total": len(by_severity.get(s, [])),
                }
                for s in _SEVERITY_ORDER
                if by_severity.get(s)
            ],
            "top_events": top_events,
            "events_display": [
                self._enrich_event(e) for e in events_display
            ],
            "loganalyzer_summary": kpis.get("loganalyzer_summary") or {},
            "loganalyzer_dashboard": kpis.get("loganalyzer_dashboard") or {},
            "generated_at": generated_at,
        }

    # ─── helpers ───

    def _group_by_severity(self, events: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for ev in events:
            bucket[(ev.get("severity") or "info").lower()].append(
                self._enrich_event(ev)
            )
        return bucket

    def _severity_buckets(
        self, events: List[Dict[str, Any]], kpis: Dict[str, Any]
    ) -> Dict[str, int]:
        # KPI 가 있으면 그대로, 없으면 events 에서 카운트.
        by_sev = kpis.get("by_severity") or {}
        if by_sev:
            return {k.lower(): int(v) for k, v in by_sev.items() if v}
        counter = Counter((ev.get("severity") or "info").lower() for ev in events)
        return dict(counter)

    def _aggregate_kv(
        self,
        kpi_map: Dict[str, int],
        events: List[Dict[str, Any]],
        key: str,
        label_map: Dict[str, str] = None,
    ) -> List[Dict[str, Any]]:
        """KPI dict 우선, 없으면 events 에서 직접 카운트. count desc 정렬."""
        if kpi_map:
            counts = {k: int(v) for k, v in kpi_map.items() if v}
        else:
            counter: Counter = Counter()
            for ev in events:
                k = ev.get(key)
                if k:
                    counter[k] += 1
            counts = dict(counter)

        result = []
        for name, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
            result.append({
                "name": name,
                "label": (label_map or {}).get(name, name),
                "count": count,
            })
        return result

    def _top_events(self, events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """심각도 우선순위 (critical > high > medium > info) + 최신 순."""
        sev_rank = {s: i for i, s in enumerate(_SEVERITY_ORDER)}

        def sort_key(ev: Dict[str, Any]):
            sev = (ev.get("severity") or "info").lower()
            occurred = ev.get("occurred_at") or ""
            return (sev_rank.get(sev, 99), -len(occurred), occurred)

        sorted_events = sorted(
            events,
            key=lambda e: (
                sev_rank.get((e.get("severity") or "info").lower(), 99),
                # 최신이 먼저 — ISO 8601 문자열은 reverse=True 로 충분
            ),
        )
        # 같은 severity 안에서는 occurred_at desc 가 되도록 2단계 정렬:
        # 위 정렬에서 severity 우선만 보장 → 이후 안정정렬로 occurred_at 처리.
        sorted_events = sorted(
            sorted_events,
            key=lambda e: e.get("occurred_at") or "",
            reverse=True,
        )
        sorted_events = sorted(
            sorted_events,
            key=lambda e: sev_rank.get(
                (e.get("severity") or "info").lower(), 99
            ),
        )
        return [self._enrich_event(e) for e in sorted_events[:8]]

    def _enrich_event(self, ev: Dict[str, Any]) -> Dict[str, Any]:
        sev = (ev.get("severity") or "info").lower()
        meta = _severity_meta(sev)
        source = ev.get("source_type") or ""
        occurred = _parse_datetime(ev.get("occurred_at"))
        return {
            **ev,
            "severity_label": meta["label"],
            "severity_color": meta["color"],
            "severity_bg": meta["bg"],
            "source_label": _SOURCE_LABEL.get(source, source or "—"),
            "occurred_at_dt": occurred,
            "occurred_at_display": occurred.strftime("%m-%d %H:%M"),
            "title_safe": ev.get("title") or "(제목 없음)",
            "raw_excerpt_safe": (ev.get("raw_excerpt") or "").strip(),
        }
