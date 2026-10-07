"""알러지 뉴스 브리핑 압축형 구성 테스트 (A4 1~1.5장 목표).

CB-T1  formatter — 헤드라인 + 기업 동향 → 「오늘의 뉴스」 단일 목록 (기업 중복 제거, 최대 3건)
CB-T2  collector — 스폿라이트 응답의 journey 를 bundle 로 전달, email_html 없으면 None
CB-T3  collector — 신규 뉴스 0건이면 최근 7일 주요 뉴스를 news_recap 으로 채움
CB-T4  template — 여정 블록이 스폿라이트 위에 삽입, 뉴스가 맨 앞
CB-T5  template — 스폿라이트 카드당 논문 1편 · 임상 함의 80자 · 처방 칩 3개
CB-T6  template — 평일은 신흥 치료법 숨김·약물은 안전 정보만, 주간판은 전체
CB-T7  template — 뉴스 0건 시 다시 보기 문구 + 오늘의 3줄
"""

import asyncio
from datetime import datetime

from src.common.template.renderer import TemplateRenderer
from src.tenant.allergy_insight.collector import AllergyInsightCollector
from src.tenant.allergy_insight.formatter import AllergyInsightFormatter, build_news_items

_FMT = AllergyInsightFormatter()

_HEADLINES = [
    {"id": 1, "title": "A사 신약 승인", "url": "http://n/1", "company_name": "A사",
     "category": "규제", "category_color": "#c62828", "summary": "가" * 120,
     "source": "약업신문", "published_at": "2026-10-07"},
    {"id": 2, "title": "B사 임상 3상", "url": "http://n/2", "company_name": "B사"},
]
_DIGEST = [
    {"company_name": "B사", "representative": {"id": 3, "title": "B사 중복", "url": "http://n/3"}},
    {"company_name": "C사", "representative": {"id": 4, "title": "C사 투자", "url": "http://n/4",
                                               "summary": "요약"}},
    {"company_name": "D사", "representative": {"id": 5, "title": "D사 출시", "url": "http://n/5"}},
    {"company_name": "E사", "representative": {"id": 6, "title": "E사 제휴", "url": "http://n/6"}},
]
_JOURNEY = {
    "paper": {"paper_id": 8042, "title": "Anaphylaxis", "title_kr": "아나필락시스"},
    "headline": "영문 초록 1,737자 → 한국어 임상 요약 164자",
    "email_html": "<table><tr><td>JOURNEY-BLOCK</td></tr></table>",
}
_CARD = {
    "allergen_code": "egg", "label_kr": "계란", "slot_type": "staleness",
    "days_since_featured": None, "paper_count": 10, "total_papers": 100,
    "mention_rate": 0.1, "change_rate": 2.0,
    "new_papers": [
        {"paper_id": 1, "title": "첫 논문", "url": "http://p/1",
         "clinical_implication": "나" * 100},
        {"paper_id": 2, "title": "둘째 논문", "url": "http://p/2"},
    ],
    "prescription": {"section": "avoid_foods", "section_label": "회피 식품",
                     "items": ["달걀", "마요네즈", "카스텔라", "머랭"], "total_items": 9},
}


def _render(**overrides):
    ctx = _FMT._empty_context()
    ctx["report_date"] = datetime(2026, 10, 7)  # 수요일
    ctx["is_weekly_edition"] = False
    ctx.update(overrides)
    return TemplateRenderer().render("allergy_insight/daily_report.html", ctx)


def test_cb_t1_news_items_merge_and_dedup():
    items = build_news_items(_HEADLINES, _DIGEST)
    assert [i["title"] for i in items] == ["A사 신약 승인", "B사 임상 3상", "C사 투자"]
    assert items[2]["label"] == "기업 동향"
    assert build_news_items([], []) == []

    ctx = _FMT.format({"daily_report": {
        "report_date": "2026-10-07T00:00:00", "top_headlines": _HEADLINES,
        "company_digest": _DIGEST, "journey": _JOURNEY,
    }})
    assert len(ctx["news_items"]) == 3
    assert ctx["journey"]["paper"]["paper_id"] == 8042
    assert ctx["is_weekly_edition"] is False
    # 발송 이력 기록용 원본 키는 그대로 유지
    assert ctx["top_headlines"] == _HEADLINES and ctx["company_digest"] == _DIGEST
    monday = _FMT.format({"daily_report": {"report_date": "2026-10-05T00:00:00"}})
    assert monday["is_weekly_edition"] is True


def _collector(handler):
    collector = AllergyInsightCollector(api_base_url="http://allergyinsight.test")

    async def fake_get(path, auth_required=False, params=None, **kwargs):
        return handler(path, params or {})

    collector._get = fake_get
    return collector


def test_cb_t2_spotlight_bundle_maps_journey():
    c = _collector(lambda p, q: {"data": {"spotlights": [_CARD], "journey": _JOURNEY}})
    bundle = asyncio.run(c._collect_spotlight_bundle())
    assert bundle["cards"][0]["label_kr"] == "계란"
    assert bundle["journey"]["email_html"].startswith("<table>")

    seen = {}

    def no_html(p, q):
        seen.update(q)
        return {"data": {"spotlights": [_CARD], "journey": {"paper": {}}}}

    c2 = _collector(no_html)
    assert asyncio.run(c2._collect_spotlight_bundle())["journey"] is None
    assert seen["max_papers"] == 1  # 카드당 대표 논문 1편
    assert asyncio.run(c2._collect_spotlight()) == [_CARD]


def test_cb_t3_news_recap_when_no_new_news():
    def handler(path, q):
        if path.endswith("/headlines/today"):
            if q.get("fallback_days") == "1,2":
                return {"data": {"headlines": [], "excluded_ids": []}}
            return {"data": {"headlines": _HEADLINES * 3, "excluded_ids": []}}
        if path.endswith("/company-digest"):
            return {"data": {"companies": []}}
        if path.endswith("/spotlight/today"):
            return {"data": {"spotlights": [_CARD], "journey": _JOURNEY}}
        raise RuntimeError(f"unmocked {path}")

    report = asyncio.run(_collector(handler).collect_daily_report())
    assert report["top_headlines"] == []
    assert len(report["news_recap"]) == 3
    assert report["journey"]["paper"]["paper_id"] == 8042


def test_cb_t4_journey_above_spotlight_and_news_first():
    html = _render(
        news_items=build_news_items(_HEADLINES, _DIGEST),
        journey=_JOURNEY, spotlights=[_CARD],
    )
    news = html.index("📰 오늘의 뉴스")
    journey = html.index("JOURNEY-BLOCK")
    spotlight = html.index("🔬 오늘의 알러지 스폿라이트")
    assert news < journey < spotlight
    assert "산업·기업 동향" not in html  # 단일 섹션으로 통합
    assert "가" * 60 + "…" in html and "가" * 61 not in html  # 뉴스 요약 한 줄(60자)


def test_cb_t5_spotlight_compact_card():
    html = _render(spotlights=[_CARD])
    assert "첫 논문" in html and "둘째 논문" not in html
    assert "나" * 80 + "…" in html and "나" * 81 not in html
    assert "카스텔라" in html and "머랭" not in html  # 처방 칩 3개
    assert "외 6건" in html  # total 9 - 노출 3
    assert "주요 연관 영역" not in html


def test_cb_t6_weekly_edition_sections():
    treatments = {"top_emerging": [{"name": "오말리주맙", "type": "biologic", "paper_count": 5}]}
    drugs = {
        "new_approvals": [{"name": "신약X", "url": "http://d/1"}],
        "label_changes": [], "blackbox_warnings": [],
        "recalls": [{"name": "회수약Y", "url": "http://d/2"}], "total": 2,
    }
    daily = _render(treatments=treatments, drug_updates=drugs)
    assert "오말리주맙" not in daily
    assert "회수약Y" in daily and "신약X" not in daily

    weekly = _render(treatments=treatments, drug_updates=drugs, is_weekly_edition=True)
    assert "오말리주맙" in weekly
    assert "회수약Y" in weekly and "신약X" in weekly

    approvals_only = dict(drugs, recalls=[], total=1)
    assert "🆕 신규 승인" not in _render(drug_updates=approvals_only)
    assert "🆕 신규 승인" in _render(drug_updates=approvals_only, is_weekly_edition=True)


def test_cb_t7_recap_notice_and_tldr():
    html = _render(news_recap=build_news_items(_HEADLINES[:1], []), journey=_JOURNEY,
                   spotlights=[_CARD])
    assert "새 소식이 없어 최근 주요 뉴스를 다시 전합니다" in html
    assert "오늘의 3줄" in html
    assert "🧭 영문 초록 1,737자" in html
    assert "🔬 스폿라이트: 계란" in html
    assert "오늘은 수집된 소식이 없습니다" not in html
