"""TechBriefing digest 테스트 — 교육·커리어 도메인.

#1 헤드라인 선별 (카테고리 다양성 + 제목 중복 제거)
#2 카테고리별 digest 그룹 구성 (교육과정/세미나·행사/정책·지원/뉴스)
#3 푸터 모집·마감 임박 + 한글 키워드 트렌드
#4 collector 분류/스코어 시그널
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.config import settings
from src.tenant.tech_briefing.collector import _classify_course, _is_recruiting
from src.tenant.tech_briefing.formatter import TechBriefingFormatter
from src.tenant.tech_briefing.scorer import score_item


@pytest.fixture(autouse=True)
def _disable_llm():
    """digest 테스트는 LLM 불필요 — Ollama 호출 차단."""
    orig_l = settings.tech_briefing_llm_enabled
    settings.tech_briefing_llm_enabled = False
    yield
    settings.tech_briefing_llm_enabled = orig_l


def _item(title, category, *, source=None, keyword="AI 교육", origin="구글뉴스",
          url=None, recruiting=False, published=None, summary=""):
    url = url or f"https://news.example/{abs(hash(title))}"
    return {
        "source": source or ("course" if category in ("course", "seminar") else category),
        "category": category,
        "keyword": keyword,
        "origin": origin,
        "title": title,
        "url": url,
        "published_at": published or datetime.now(timezone.utc),
        "summary": summary,
        "is_recruiting": recruiting,
        "dedup_key": f"{category}:{url}",
    }


def _payload(news=(), policy=(), course=()):
    return {
        "tech_daily": {
            "report_date": datetime.now(timezone.utc).isoformat(),
            "news_items": list(news),
            "policy_items": list(policy),
            "course_items": list(course),
            "stats": {
                "news_count": len(news),
                "policy_count": len(policy),
                "course_count": len(course),
            },
        }
    }


# ─── collector 시그널 ───────────────────────────────────────────────

def test_classify_course_seminar_hints():
    assert _classify_course("생성형 AI 컨퍼런스 개최") == "seminar"
    assert _classify_course("AI 실무 Webinar 안내") == "seminar"
    assert _classify_course("KDT 부트캠프 5기 모집") == "course"


def test_is_recruiting_hints():
    assert _is_recruiting("AI 부트캠프 수강생 모집") is True
    assert _is_recruiting("지원사업 접수 마감 임박") is True
    assert _is_recruiting("AI 교육 시장 동향 분석") is False


# ─── scorer ─────────────────────────────────────────────────────────

def test_score_policy_beats_news():
    """같은 조건이면 정책(공식 출처) > 뉴스."""
    now = datetime.now(timezone.utc)
    policy = score_item({
        "category": "policy", "origin": "정책브리핑",
        "url": "https://www.korea.kr/x", "published_at": now,
    })
    news = score_item({
        "category": "news", "origin": "구글뉴스",
        "url": "https://news.example/y", "published_at": now,
    })
    assert policy > news


def test_score_recruiting_boost():
    now = datetime.now(timezone.utc)
    base = {"category": "course", "origin": "구글뉴스", "published_at": now}
    plain = score_item(dict(base))
    recruiting = score_item(dict(base, is_recruiting=True))
    assert recruiting == pytest.approx(plain + 1.0)


def test_score_age_penalty():
    fresh = score_item({
        "category": "news",
        "published_at": datetime.now(timezone.utc),
    })
    stale = score_item({
        "category": "news",
        "published_at": datetime.now(timezone.utc) - timedelta(days=10),
    })
    assert fresh - stale == pytest.approx(2.0, abs=0.1)  # cap 2.0


# ─── formatter: 헤드라인 ────────────────────────────────────────────

def test_headline_category_diversity():
    """같은 카테고리는 최대 2건 — 나머지 슬롯은 다른 카테고리로."""
    course = [_item(f"부트캠프 {i} 모집", "course", recruiting=True) for i in range(5)]
    news = [_item(f"AI 뉴스 {i}", "news") for i in range(3)]
    ctx = TechBriefingFormatter().format(_payload(news=news, course=course))

    categories = [h["category"] for h in ctx["headlines"]]
    assert categories.count("course") <= 2
    assert "news" in categories
    assert len(ctx["headlines"]) <= 5


def test_headline_duplicate_title_removed():
    """같은 기사가 여러 키워드에서 잡혀도 제목 기준 1건만."""
    dup1 = _item("AI 부트캠프 대규모 모집", "course", url="https://a.example/1")
    dup2 = _item("AI 부트캠프 대규모 모집", "course", url="https://b.example/2")
    ctx = TechBriefingFormatter().format(_payload(course=[dup1, dup2]))
    titles = [h["title_safe"] for h in ctx["headlines"]]
    assert titles.count("AI 부트캠프 대규모 모집") == 1


# ─── formatter: digest 그룹 ─────────────────────────────────────────

def test_digest_groups_by_category():
    """헤드라인 제외분이 카테고리 그룹으로 배치되고 한글 라벨을 단다."""
    course = [_item(f"교육 {i}", "course") for i in range(4)]
    seminar = [_item(f"세미나 {i} 컨퍼런스", "seminar") for i in range(3)]
    policy = [_item(f"정책 {i}", "policy", origin="정책브리핑") for i in range(3)]
    news = [_item(f"뉴스 {i}", "news") for i in range(3)]
    ctx = TechBriefingFormatter().format(
        _payload(news=news, policy=policy, course=course + seminar)
    )

    labels = [g["label"] for g in ctx["digest_groups"]]
    assert set(labels) <= {"교육과정", "세미나·행사", "정책·지원", "뉴스"}
    assert ctx["digest_total"] == sum(len(g["entries"]) for g in ctx["digest_groups"])
    # 헤드라인과 digest 는 겹치지 않음
    headline_keys = {h["dedup_key"] for h in ctx["headlines"]}
    for g in ctx["digest_groups"]:
        for e in g["entries"]:
            assert e["dedup_key"] not in headline_keys


def test_digest_group_cap():
    course = [_item(f"교육 {i}", "course") for i in range(20)]
    ctx = TechBriefingFormatter().format(_payload(course=course))
    for g in ctx["digest_groups"]:
        assert len(g["entries"]) <= TechBriefingFormatter.DIGEST_PER_GROUP_LIMIT


# ─── formatter: 푸터 ────────────────────────────────────────────────

def test_footer_recruiting_list():
    recruiting = [_item(f"부트캠프 {i}기 모집", "course", recruiting=True) for i in range(8)]
    plain = [_item("AI 교육 동향", "news")]
    ctx = TechBriefingFormatter().format(_payload(news=plain, course=recruiting))

    footer = ctx["footer_extras"]["recruiting"]
    assert 0 < len(footer) <= TechBriefingFormatter.FOOTER_RECRUITING_LIMIT
    assert all(r["is_recruiting"] for r in footer)


def test_keyword_trend_extracts_hangul():
    items = [
        _item(f"엔비디아 채용연계 과정 {i}", "course",
              summary="엔비디아 협력 채용연계 교육") for i in range(3)
    ]
    ctx = TechBriefingFormatter().format(_payload(course=items))
    keywords = [k["keyword"] for k in ctx["footer_extras"]["keywords"]]
    assert "엔비디아" in keywords
    # 검색 키워드 구성어(교육 등)는 stopword 로 제외
    assert "교육" not in keywords


# ─── formatter: 빈 입력 ─────────────────────────────────────────────

def test_empty_payload_yields_empty_context():
    ctx = TechBriefingFormatter().format({})
    assert ctx["headlines"] == []
    assert ctx["digest_groups"] == []
    assert ctx["stats"]["total_items"] == 0
