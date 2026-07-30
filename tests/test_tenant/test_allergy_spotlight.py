"""알러지 스폿라이트 다중 카드 수집·렌더 테스트.

선정 로직은 AllergyInsight 서버(`spotlight_service`)로 이관됐다. 여기서는
collector 의 소비·폴백과 템플릿 렌더링을 검증한다.

SP-T1  collector — 서버 응답을 카드 리스트로 전달
SP-T2  collector — label_kr 누락 시 로컬 매핑 폴백
SP-T3  collector — API 실패 시 빈 리스트 폴백 (예외 미전파)
SP-T4  collector — 라벨맵에 tree_nuts(복수)/pet_dander/bee_venom 존재
SP-T5  formatter — spotlights 패스스루 + 빈 컨텍스트 기본값
SP-T6  template — 다중 카드 · 논문 · 처방 섹션 렌더
SP-T7  template — 구 단일 spotlight 컨텍스트 폴백 렌더
SP-T8  template — spotlights 없으면 섹션 전체 숨김
"""

import asyncio

import pytest

from src.common.template.renderer import TemplateRenderer
from src.tenant.allergy_insight.collector import AllergyInsightCollector
from src.tenant.allergy_insight.formatter import AllergyInsightFormatter

_FMT = AllergyInsightFormatter()

_SERVER_RESPONSE = {
    "data": {
        "spotlights": [
            {
                "allergen_code": "dust_mite",
                "label_kr": "집먼지진드기",
                "slot_type": "staleness",
                "last_featured_date": None,
                "days_since_featured": None,
                "year": 2026,
                "paper_count": 42,
                "total_papers": 1580,
                "mention_rate": 0.0266,
                "change_rate": -3.1,
                "trend_direction": "stable",
                "top_link_types": [
                    {"type": "symptom", "label": "증상", "count": 12},
                ],
                "new_papers": [
                    {
                        "paper_id": 881,
                        "title": "집먼지진드기 설하면역치료 3년 추적",
                        "journal": "J Allergy Clin Immunol",
                        "year": 2026,
                        "url": "https://pubmed.example/881",
                        "clinical_implication": "3년 유지 시 증상 점수 42% 감소.",
                        "link_type": "treatment",
                        "link_type_label": "치료",
                        "relevance_score": 92,
                    },
                ],
                "prescription": {
                    "section": "avoid_exposure",
                    "section_label": "노출 회피",
                    "items": ["침구 주 1회 60℃ 이상 세탁", "카펫·천 소파 제거"],
                    "total_items": 7,
                },
            },
            {
                "allergen_code": "fish",
                "label_kr": "어류",
                "slot_type": "trending",
                "last_featured_date": "2026-07-22",
                "days_since_featured": 8,
                "year": 2026,
                "paper_count": 88,
                "total_papers": 1580,
                "mention_rate": 0.0557,
                "change_rate": 34.2,
                "trend_direction": "rising",
                "top_link_types": [],
                "new_papers": [],
                "prescription": {
                    "section": "substitutes",
                    "section_label": "대체 식품",
                    "items": ["생선 → 닭고기, 두부"],
                    "total_items": 1,
                },
            },
        ],
    },
    "meta": {
        "report_date": "2026-07-30",
        "pool_size": 16,
        "selected": 2,
        "recorded": True,
        "replayed": False,
        "skipped_no_content": ["latex"],
    },
}


def _collector_with_response(response):
    """_get 을 스텁으로 교체한 collector. response 가 Exception 이면 raise."""
    collector = AllergyInsightCollector(api_base_url="http://allergyinsight.test")

    async def fake_get(path, auth_required=False, params=None, **kwargs):
        if isinstance(response, Exception):
            raise response
        return response

    collector._get = fake_get
    return collector


# --------------------------------------------------------------------------
# collector
# --------------------------------------------------------------------------

def test_sp_t1_collector_returns_card_list():
    collector = _collector_with_response(_SERVER_RESPONSE)

    cards = asyncio.run(collector._collect_spotlight())

    assert len(cards) == 2
    assert [c["allergen_code"] for c in cards] == ["dust_mite", "fish"]
    assert cards[0]["label_kr"] == "집먼지진드기"
    assert cards[0]["new_papers"][0]["paper_id"] == 881
    assert cards[0]["prescription"]["section_label"] == "노출 회피"
    assert cards[1]["slot_type"] == "trending"

    metrics = collector.drain_metrics()
    spotlight_metric = [m for m in metrics if m["data_type"] == "spotlight"]
    assert len(spotlight_metric) == 1
    assert spotlight_metric[0]["final_count"] == 2


def test_sp_t2_label_falls_back_to_local_map():
    """서버가 label_kr 을 주지 않아도 영문 코드가 노출되지 않는다."""
    response = {
        "data": {
            "spotlights": [
                {"allergen_code": "tree_nuts", "new_papers": [], "prescription": None},
            ],
        },
        "meta": {},
    }
    collector = _collector_with_response(response)

    cards = asyncio.run(collector._collect_spotlight())

    assert cards[0]["label_kr"] == "견과류"


def test_sp_t3_api_failure_returns_empty_list():
    collector = _collector_with_response(RuntimeError("connection refused"))

    cards = asyncio.run(collector._collect_spotlight())

    assert cards == []
    metric = [m for m in collector.drain_metrics() if m["data_type"] == "spotlight"][0]
    assert "connection refused" in metric["error"]


def test_sp_t4_label_map_covers_plural_and_missing_codes():
    """트렌드 데이터가 쓰는 코드가 라벨맵에 있어야 영문 노출이 없다.

    tree_nuts(복수)·pet_dander·bee_venom 누락으로 이메일에 영문 코드가
    그대로 나갔던 버그의 회귀 테스트.
    """
    collector = AllergyInsightCollector(api_base_url="http://allergyinsight.test")

    for code in ("tree_nuts", "pet_dander", "bee_venom", "shellfish", "dust_mite"):
        assert collector._allergen_label(code) != code, f"{code} 라벨 누락"


# --------------------------------------------------------------------------
# formatter
# --------------------------------------------------------------------------

def test_sp_t5_formatter_passes_through_spotlights():
    daily_report = {
        "report_date": "2026-07-30T00:00:00+00:00",
        "generated_at": "2026-07-30T00:00:00+00:00",
        "spotlights": _SERVER_RESPONSE["data"]["spotlights"],
        "spotlight": _SERVER_RESPONSE["data"]["spotlights"][0],
    }

    context = _FMT.format({"daily_report": daily_report})

    assert len(context["spotlights"]) == 2
    # 구 소비자 호환 키 유지
    assert context["spotlight"]["allergen_code"] == "dust_mite"

    empty = _FMT._empty_context()
    assert empty["spotlights"] == []
    assert empty["spotlight"] is None


# --------------------------------------------------------------------------
# template
# --------------------------------------------------------------------------

def _render(context_overrides):
    renderer = TemplateRenderer()
    context = _FMT._empty_context()
    context.update(context_overrides)
    return renderer.render("allergy_insight/daily_report.html", context)


def test_sp_t6_template_renders_all_cards():
    html = _render({"spotlights": _SERVER_RESPONSE["data"]["spotlights"]})

    assert "오늘의 알러지 스폿라이트" in html
    # 두 카드 모두 렌더
    assert "집먼지진드기" in html
    assert "어류" in html
    # 논문 섹션
    assert "집먼지진드기 설하면역치료 3년 추적" in html
    assert "https://pubmed.example/881" in html
    assert "3년 유지 시 증상 점수 42% 감소." in html
    # 처방 섹션
    assert "노출 회피" in html
    assert "침구 주 1회 60℃ 이상 세탁" in html
    assert "외 5건" in html  # total_items 7 - 노출 2
    # 슬롯 배지 — 트렌드 슬롯은 재노출 간격보다 '상승 트렌드' 표기를 우선한다
    assert "첫 소개" in html
    assert "상승 트렌드" in html
    # 영문 코드가 라벨 대신 노출되지 않음
    assert "dust_mite" not in html


def test_sp_t6b_staleness_card_shows_days_since_featured():
    """스테일니스 슬롯의 재노출은 경과일 배지로 표기한다."""
    html = _render({
        "spotlights": [{
            "allergen_code": "milk",
            "label_kr": "우유",
            "slot_type": "staleness",
            "days_since_featured": 12,
            "last_featured_date": "2026-07-18",
            "paper_count": 30,
            "total_papers": 1580,
            "mention_rate": 0.019,
            "change_rate": 2.0,
            "trend_direction": "stable",
            "top_link_types": [],
            "new_papers": [],
            "prescription": None,
        }],
    })

    assert "12일 만에 다시 보기" in html
    assert "상승 트렌드" not in html


def test_sp_t7_template_falls_back_to_single_spotlight():
    """spotlights 가 없고 구 spotlight 키만 있어도 렌더된다."""
    html = _render({
        "spotlights": [],
        "spotlight": _SERVER_RESPONSE["data"]["spotlights"][0],
    })

    assert "오늘의 알러지 스폿라이트" in html
    assert "집먼지진드기" in html


def test_sp_t8_template_hides_section_when_empty():
    html = _render({"spotlights": [], "spotlight": None})

    assert "오늘의 알러지 스폿라이트" not in html


def test_sp_t9_template_handles_missing_optional_fields():
    """논문·처방·트렌드 지표가 모두 없어도 예외 없이 렌더된다."""
    html = _render({
        "spotlights": [{
            "allergen_code": "latex",
            "label_kr": "라텍스",
            "slot_type": "staleness",
            "days_since_featured": None,
            "paper_count": 0,
            "total_papers": 0,
            "mention_rate": 0.0,
            "change_rate": None,
            "trend_direction": None,
            "top_link_types": [],
            "new_papers": [],
            "prescription": None,
        }],
    })

    assert "라텍스" in html
    # change_rate=None 은 대시로 표기 (0% 로 오인되지 않게)
    assert "—" in html


def test_sp_t10_negative_change_rate_rendered_with_sign():
    html = _render({"spotlights": _SERVER_RESPONSE["data"]["spotlights"]})

    assert "-3.1" in html   # dust_mite 하락
    assert "+34.2" in html  # fish 상승
