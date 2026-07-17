"""TechBriefing Phase 3 — 교차일 dedup + weekly 집계 테스트.

#1 dedup_id 안정성 (동일 입력 → 동일 63-bit 양수)
#2 collect_daily exclude_ids 원천 제외
#3 extract_sent_article_entries (headlines + digest entries)
#4 format_weekly 이력 집계 (헤드라인/모집 D-day/일별 추이/WoW Δ)
"""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.tenant.tech_briefing import TechBriefingTenant
from src.tenant.tech_briefing.collector import TechBriefingCollector, dedup_id_for
from src.tenant.tech_briefing.formatter import TechBriefingFormatter


def _resource(rid: str, rtype: str, title: str, *, deadline=None, published=None):
    published = published or datetime.now(timezone.utc).isoformat()
    return {
        "id": rid, "type": rtype, "title": title,
        "summary": f"{title} 요약", "provider": "test",
        "cost": None, "deadline": deadline, "tags": [], "audience": [],
        "url": f"https://example.com/{rid}", "published_at": published,
    }


def _payload(resources_by_section):
    sections = {k: [] for k in ("course", "seminar", "policy", "news")}
    sections.update(resources_by_section)
    return {
        "date": "2026-07-17", "headline": "t", "summary": None,
        "stats": {k: len(v) for k, v in sections.items()},
        "generated_at": None, "sections": sections, "fallback": False,
    }


# ─── #1 dedup_id ───────────────────────────────────────────────────

def test_dedup_id_stable_and_positive():
    a = dedup_id_for("uuid-1234")
    assert a == dedup_id_for("uuid-1234")          # 안정
    assert a != dedup_id_for("uuid-1235")          # 구분
    assert 0 < a < 2 ** 63                          # Integer 컬럼 안전


# ─── #2 exclude_ids 원천 제외 ──────────────────────────────────────

@pytest.mark.parametrize("excluded", [True, False])
def test_collect_daily_excludes_recent_ids(excluded):
    payload = _payload({"news": [_resource("n1", "news", "뉴스 A")]})
    collector = TechBriefingCollector(api_base_url="http://test:9070")
    exclude = [dedup_id_for("n1")] if excluded else None

    with patch.object(
        TechBriefingCollector, "_fetch_daily", new=AsyncMock(return_value=payload)
    ), patch("src.tenant.tech_briefing.collector.settings") as mock_settings:
        mock_settings.skillradar_newsletter_key = "k"
        import asyncio
        data = asyncio.run(collector.collect_daily(exclude_ids=exclude))

    if excluded:
        assert data == {}  # 유일 항목 제외 → 빈 결과
        metric = collector.drain_metrics()[0]
        assert metric["excluded_by_ids"] == 1
    else:
        assert len(data["tech_daily"]["news_items"]) == 1
        assert data["tech_daily"]["news_items"][0]["dedup_id"] == dedup_id_for("n1")


# ─── #3 extract_sent_article_entries ───────────────────────────────

def test_extract_sent_article_entries():
    tenant = TechBriefingTenant()
    context = {
        "headlines": [
            {"dedup_id": 11, "url": "https://a"},
            {"dedup_id": None, "url": "https://skip"},  # id 없으면 스킵
        ],
        "digest_groups": [
            {"entries": [{"dedup_id": 22, "url": "https://b"}]},
        ],
    }
    entries = tenant.extract_sent_article_entries(context)
    assert (11, "https://a", "headline", None) in entries
    assert (22, "https://b", "digest", None) in entries
    assert len(entries) == 2
    assert tenant.dedup_recent_days == 7


# ─── #4 format_weekly ──────────────────────────────────────────────

def _history_row(day: date, items_per_type=2):
    def mk(bucket, cat, n):
        return [{
            "source": bucket, "category": cat, "keyword": "", "origin": "test",
            "title": f"{cat} {day} {i}", "url": f"https://x/{cat}/{day}/{i}",
            "published_at": datetime.now(timezone.utc).isoformat(),
            "summary": "혁신 전략 요약", "is_recruiting": cat == "course",
            "dedup_key": f"{cat}:{day}:{i}", "dedup_id": hash((cat, day, i)) % 2**62,
            "deadline": (date.today() + timedelta(days=2)).isoformat() if cat == "course" else None,
        } for i in range(n)]

    return {
        "collected_date": day,
        "data_type": "tech_daily",
        "data": {
            "report_date": str(day),
            "news_items": mk("news", "news", items_per_type),
            "policy_items": mk("policy", "policy", items_per_type),
            "course_items": mk("course", "course", items_per_type),
            "stats": {
                "news_count": items_per_type,
                "policy_count": items_per_type,
                "course_count": items_per_type,
            },
        },
    }


def test_format_weekly_aggregates_history():
    monday = date(2026, 7, 13)
    history = [_history_row(monday + timedelta(days=d)) for d in range(5)]
    prev_history = [_history_row(monday - timedelta(days=7 - d), items_per_type=1)
                    for d in range(5)]

    ctx = TechBriefingFormatter().format_weekly(
        history, {"_prev_history": prev_history,
                  "tech_weekly": {"skillradar_stats": {"resources_total": 99}}},
    )

    assert ctx["report_range"]["from"] == monday
    assert ctx["totals"]["total"] == 30            # 5일 × 3종 × 2건
    assert ctx["totals"]["prev_total"] == 15       # 전주 5일 × 3종 × 1건
    assert ctx["totals"]["delta"] == 15
    assert len(ctx["daily_trend"]) == 5
    assert ctx["trend_max"] == 6
    assert 0 < len(ctx["week_headlines"]) <= 8
    # course 는 모집 신호 + 미래 deadline → D-day 부여
    assert ctx["recruiting"] and all(
        r.get("d_day") is not None for r in ctx["recruiting"]
    )
    assert ctx["skillradar_stats"]["resources_total"] == 99


def test_format_weekly_empty_history_returns_empty():
    assert TechBriefingFormatter().format_weekly([], {}) == {}
    assert TechBriefingTenant().format_summary_report("monthly", [], {}) == {}
