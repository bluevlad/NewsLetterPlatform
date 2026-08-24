"""TechBriefing StandUp Ops Insight 섹션 테스트 (Phase 2).

#1 build_ops_context — payload → 템플릿 컨텍스트 (KPI 칩/이벤트/검증)
#2 collector.collect_ops_insight — carry-over dedup + 빈 키 보장
#3 extract_sent_article_entries — ops dedup_id 기록
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.tenant.tech_briefing import TechBriefingTenant
from src.tenant.tech_briefing.collector import TechBriefingCollector, dedup_id_for
from src.tenant.tech_briefing.config import tenant_settings as settings
from src.tenant.tech_briefing.ops_insight import build_ops_context


NL_ID = "2f3a1c9e-0000-4000-8000-123456789abc"


def _payload(**over):
    base = {
        "newsletter_id": NL_ID,
        "dedup_id": dedup_id_for(f"standup:{NL_ID}"),
        "period_start": "2026-08-17",
        "period_end": "2026-08-23",
        "headline": "AllergyInsight exception 급증",
        "subject": "[StandUp Insight] ...",
        "kpis": {
            "total_events": 20,
            "by_service": {"AllergyInsight": 12, "Gateway": 3},
            "loganalyzer_summary": {
                "total_errors": 291, "critical": 95, "high": 7,
                "open_groups": 55839,
            },
            "fix_verifications": [
                {"fix_title": "fix(api): None 처리", "status": "verified",
                 "fingerprint": "534c11c4240a341e", "recurrence_count": 0},
                {"fix_title": "fix(gw): proxy 재시도", "status": "recurred",
                 "fingerprint": "9d3837dc212e6c26", "recurrence_count": 3},
                {"fix_title": "fix(ui): 라벨", "status": "unlinked",
                 "fingerprint": None, "recurrence_count": 0},
            ],
        },
        "events": [
            {"title": "AllergyInsight · exception (×1335)", "severity": "critical",
             "service_tag": "AllergyInsight", "source_type": "loganalyzer",
             "occurred_at": "2026-08-23T10:00:00+00:00"},
            {"title": "Gateway · proxy_error", "severity": "high",
             "service_tag": "Gateway", "source_type": "loganalyzer",
             "occurred_at": "2026-08-22T08:00:00+00:00"},
            {"title": "journal 수집", "severity": "info",
             "service_tag": "", "source_type": "auto_tobe_journal",
             "occurred_at": "2026-08-21T02:00:00+00:00"},
        ],
    }
    base.update(over)
    return base


# ─── #1 build_ops_context ───────────────────────────────────────────────

def test_build_ops_context_empty_payload_returns_none():
    assert build_ops_context(None) is None
    assert build_ops_context({}) is None


def test_build_ops_context_kpi_chips_and_services():
    ctx = build_ops_context(_payload())
    labels = {c["label"]: c["value"] for c in ctx["kpi_chips"]}
    assert labels["총 오류"] == 291
    assert labels["Critical"] == 95
    assert labels["수집 이벤트"] == 20
    assert ctx["by_service"][0] == {"name": "AllergyInsight", "count": 12}
    assert ctx["period_display"] == "08-17 ~ 08-23"
    assert ctx["dedup_id"] == dedup_id_for(f"standup:{NL_ID}")


def test_build_ops_context_top_events_severity_first():
    ctx = build_ops_context(_payload())
    events = ctx["top_events"]
    assert events[0]["severity"] == "critical"
    assert events[0]["severity_label"] == "Critical"
    assert events[1]["severity"] == "high"
    assert events[2]["source_label"] == "Auto-Tobe"


def test_build_ops_context_verification_meta():
    ctx = build_ops_context(_payload())
    v = {x["status"]: x for x in ctx["verifications"]}
    assert v["verified"]["icon"] == "✅"
    assert v["recurred"]["icon"] == "❌"
    assert v["recurred"]["recurrence_count"] == 3
    assert v["unlinked"]["icon"] == "➖"


def test_build_ops_context_headline_fallback():
    ctx = build_ops_context(_payload(headline=None))
    assert ctx["headline"] == "주간 StandUp Insight 요약"


# ─── #2 collector.collect_ops_insight ───────────────────────────────────

@pytest.fixture()
def _standup_url():
    orig = settings.standup_api_url
    settings.standup_api_url = "http://standup.test:9065"
    yield
    settings.standup_api_url = orig


def _run(coro):
    return asyncio.run(coro)


def test_collect_ops_insight_disabled_without_url():
    orig = settings.standup_api_url
    settings.standup_api_url = ""
    try:
        result = _run(TechBriefingCollector().collect_ops_insight())
        assert result == {"ops_insight": {}}
    finally:
        settings.standup_api_url = orig


def _mock_get_json(newsletters, events=None):
    async def fake(path, params):
        if "newsletters" in path:
            return newsletters
        return events or []
    return fake


def test_collect_ops_insight_picks_latest_unpublished(_standup_url):
    newsletters = [
        {"id": NL_ID, "period_start": "2026-08-17", "period_end": "2026-08-23",
         "headline": "H1", "subject": "S1", "kpis": {"total_events": 5}},
    ]
    with patch(
        "src.tenant.tech_briefing.collector.httpx.AsyncClient"
    ) as client_cls:
        client = client_cls.return_value.__aenter__.return_value
        resp = AsyncMock()
        client.get = AsyncMock(return_value=resp)
        resp.raise_for_status = lambda: None
        resp.json = lambda: newsletters
        result = _run(TechBriefingCollector().collect_ops_insight())
    ops = result["ops_insight"]
    assert ops["newsletter_id"] == NL_ID
    assert ops["dedup_id"] == dedup_id_for(f"standup:{NL_ID}")
    assert ops["kpis"] == {"total_events": 5}


def test_collect_ops_insight_excludes_already_published(_standup_url):
    newsletters = [
        {"id": NL_ID, "headline": "H1", "kpis": {}},
    ]
    with patch(
        "src.tenant.tech_briefing.collector.httpx.AsyncClient"
    ) as client_cls:
        client = client_cls.return_value.__aenter__.return_value
        resp = AsyncMock()
        client.get = AsyncMock(return_value=resp)
        resp.raise_for_status = lambda: None
        resp.json = lambda: newsletters
        result = _run(TechBriefingCollector().collect_ops_insight(
            exclude_ids=[dedup_id_for(f"standup:{NL_ID}")]
        ))
    assert result == {"ops_insight": {}}


def test_collect_ops_insight_api_failure_returns_empty(_standup_url):
    async def fail_once(fn, **kwargs):
        return await fn()

    with patch(
        "src.tenant.tech_briefing.collector.retry_async", fail_once
    ), patch(
        "src.tenant.tech_briefing.collector.httpx.AsyncClient"
    ) as client_cls:
        client = client_cls.return_value.__aenter__.return_value
        client.get = AsyncMock(side_effect=RuntimeError("connection refused"))
        collector = TechBriefingCollector()
        result = _run(collector.collect_ops_insight())
    assert result == {"ops_insight": {}}
    metrics = collector.drain_metrics()
    assert any(m["data_type"] == "standup_ops" and m["error"] for m in metrics)


# ─── #3 extract_sent_article_entries ────────────────────────────────────

def test_extract_sent_article_entries_includes_ops():
    tenant = TechBriefingTenant()
    ctx = {
        "headlines": [{"dedup_id": 111, "url": "https://a"}],
        "digest_groups": [],
        "ops_insight": {"dedup_id": 999, "newsletter_id": NL_ID},
    }
    entries = tenant.extract_sent_article_entries(ctx)
    assert (111, "https://a", "headline", None) in entries
    assert (999, None, "ops_insight", None) in entries


def test_extract_sent_article_entries_ops_none_safe():
    tenant = TechBriefingTenant()
    entries = tenant.extract_sent_article_entries(
        {"headlines": [], "digest_groups": [], "ops_insight": None}
    )
    assert entries == []
