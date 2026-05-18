"""TechBriefing Service Profile + relevance 스코어링 스모크 테스트."""

from src.tenant.tech_briefing.service_profiles import (
    KnownDebt,
    ServiceProfile,
    SignalWeights,
    load_profiles,
    reset_cache,
)
from src.tenant.tech_briefing.scorer import (
    annotate_scores,
    evaluate_relevance,
    score_item,
)


def test_load_hopenvision_profile():
    reset_cache()
    profiles = load_profiles()
    services = {p.service for p in profiles}
    assert "hopenvision" in services, f"hopenvision 프로파일 누락: {services}"

    hv = next(p for p in profiles if p.service == "hopenvision")
    assert hv.high_interest, "high_interest 시그널이 비어 있음"
    # 대표 시그널 몇 개 검증
    high = [s.lower() for s in hv.high_interest]
    assert any("spring boot" in s for s in high)
    assert any("react 19" in s for s in high)


def test_relevance_high_interest_match():
    """Spring Boot 3.3 release 가 hopenvision high_interest 매칭으로 양의 점수."""
    profile = ServiceProfile(
        service="hopenvision",
        high_interest=("Spring Boot 3", "PostgreSQL"),
        low_interest=("Android",),
        known_debt=(),
        weights=SignalWeights(),
    )
    item = {
        "title": "Spring Boot 3.3 GA released",
        "summary": "PostgreSQL 16 호환성 강화 및 GC 개선.",
    }
    result = evaluate_relevance(item, profile)
    assert result["score"] > 0
    assert "Spring Boot 3" in result["matched_high"]
    assert "PostgreSQL" in result["matched_high"]
    assert result["matched_low"] == []
    assert "관심" in result["reason"]


def test_relevance_low_interest_penalty():
    profile = ServiceProfile(
        service="hopenvision",
        high_interest=("React 19",),
        low_interest=("Android",),
        known_debt=(),
        weights=SignalWeights(),
    )
    item = {
        "title": "React Native 0.78 for Android performance",
        "summary": "Android 빌드 시간 단축.",
    }
    result = evaluate_relevance(item, profile)
    assert result["score"] < 0, f"low_interest 매칭은 음수여야 함: {result}"


def test_relevance_known_debt_match():
    profile = ServiceProfile(
        service="hopenvision",
        high_interest=(),
        low_interest=(),
        known_debt=(KnownDebt(area="MariaDB → PostgreSQL 마이그레이션"),),
        weights=SignalWeights(),
    )
    item = {
        "title": "MariaDB → PostgreSQL 마이그레이션 자동화 도구",
        "summary": "ddl 변환 패턴 정리.",
    }
    result = evaluate_relevance(item, profile)
    assert result["score"] > 0
    assert result["matched_debt"]


def test_relevance_high_cap():
    """high_interest 다수 매칭되어도 high_cap 으로 상한."""
    profile = ServiceProfile(
        service="hopenvision",
        high_interest=("Spring", "Boot", "Java", "JPA", "JWT"),
        weights=SignalWeights(per_high_interest=2.0, high_cap=4.0),
    )
    item = {"title": "Spring Boot Java JPA JWT all together"}
    result = evaluate_relevance(item, profile)
    assert result["score"] == 4.0  # cap 적용


def test_annotate_scores_adds_relevance_keys():
    """annotate_scores 가 importance + service_relevance + relevance_max 모두 부여."""
    items = [
        {
            "source": "github_release",
            "tier": "S",
            "title": "Spring Boot 3.3 released",
            "summary": "PostgreSQL 호환성 강화.",
            "published_at": None,
            "is_breaking": False,
        },
    ]
    annotate_scores(items)
    item = items[0]
    assert "importance_score" in item
    assert 0.0 <= item["importance_score"] <= 10.0
    assert "service_relevance" in item
    assert "relevance_max" in item
    # 프로파일이 로드되었다면 hopenvision 키가 있어야 함
    profiles = load_profiles()
    if profiles:
        assert "hopenvision" in item["service_relevance"]
        # Spring Boot 3 매칭 → 양의 점수
        assert item["service_relevance"]["hopenvision"]["score"] > 0
        assert item["relevance_max"] > 0


def test_no_match_yields_zero():
    """매칭 없는 아이템 → relevance_max = 0, importance 만으로 정렬됨."""
    items = [
        {
            "source": "rss_blog",
            "tier": "B",
            "title": "어떤 일반 블로그 글입니다",
            "summary": "특별한 기술 키워드 없음.",
            "published_at": None,
        },
    ]
    annotate_scores(items)
    rel = items[0]["service_relevance"].get("hopenvision", {})
    assert rel.get("score", 0.0) == 0.0
    assert items[0]["relevance_max"] == 0.0


def test_score_item_unchanged_signature():
    """기존 score_item 시그니처 보존 — 회귀 방지."""
    score = score_item({
        "source": "nvd_cve",
        "tier": "S",
        "cvss": 9.5,
        "published_at": None,
    })
    assert 0.0 <= score <= 10.0
