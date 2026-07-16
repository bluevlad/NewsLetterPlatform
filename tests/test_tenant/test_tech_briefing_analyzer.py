"""TechBriefing analyzer + ollama_client + prompts 단위 테스트 (Ollama mock).

교육·커리어 도메인 전환 후 — Service Profile 없이 전 헤드라인 분석,
스키마 what_it_is / who_benefits / recommendation(APPLY|PLAN|WATCH|SKIP) / action_tip.
"""

from unittest.mock import patch

from src.tenant.tech_briefing.analyzer import (
    _normalize_analysis,
    analyze_headlines,
)
from src.tenant.tech_briefing.ollama_client import GenResult, parse_json_response
from src.tenant.tech_briefing.prompts import render_user_prompt


def test_parse_json_with_code_fence():
    text = '```json\n{"a": 1, "b": "x"}\n```'
    assert parse_json_response(text) == {"a": 1, "b": "x"}


def test_parse_json_with_prose_wrapping():
    text = '여기 결과입니다:\n{"k": "v"}\n끝.'
    assert parse_json_response(text) == {"k": "v"}


def test_parse_json_invalid_returns_none():
    assert parse_json_response("no json here at all") is None
    assert parse_json_response("") is None


def test_normalize_full_response():
    raw = {
        "what_it_is": "KDT 부트캠프 모집 소식",
        "who_benefits": "취준생에게 유용",
        "recommendation": {"level": "APPLY", "rationale": "모집 마감 임박"},
        "action_tip": "공식 페이지에서 신청",
    }
    out = _normalize_analysis(raw)
    assert out["recommendation"]["level"] == "APPLY"
    assert out["what_it_is"] == "KDT 부트캠프 모집 소식"
    assert out["action_tip"] == "공식 페이지에서 신청"


def test_normalize_invalid_level_defaults_to_watch():
    raw = {
        "what_it_is": "X",
        "recommendation": {"level": "URGENT", "rationale": "..."},
    }
    out = _normalize_analysis(raw)
    assert out["recommendation"]["level"] == "WATCH"


def test_normalize_missing_what_it_is_returns_none():
    raw = {"who_benefits": "..."}
    assert _normalize_analysis(raw) is None


def test_normalize_non_dict_returns_none():
    assert _normalize_analysis(None) is None
    assert _normalize_analysis("string") is None


def test_render_user_prompt_includes_item_fields():
    item = {
        "source": "course", "category": "course",
        "keyword": "KDT 국비지원", "origin": "구글뉴스",
        "title": "AI 부트캠프 5기 모집", "url": "https://x",
        "summary": "모집 요강", "published_at": "2026-07-15",
        "is_recruiting": True,
    }
    text = render_user_prompt(item=item)
    assert "AI 부트캠프 5기 모집" in text
    assert "KDT 국비지원" in text
    assert "recruiting" in text          # 모집 신호 블록
    assert "취업준비생" in text            # 독자 프로필


def test_render_user_prompt_without_recruiting_block():
    item = {
        "source": "news", "category": "news",
        "keyword": "AI 교육", "origin": "구글뉴스",
        "title": "AI 교육 시장 동향", "url": "https://y",
        "summary": "동향 기사", "published_at": "2026-07-15",
        "is_recruiting": False,
    }
    text = render_user_prompt(item=item)
    assert "recruiting" not in text


def test_analyze_headlines_skips_when_disabled(monkeypatch):
    monkeypatch.setattr(
        "src.tenant.tech_briefing.analyzer.settings.tech_briefing_llm_enabled",
        False,
    )
    items = [{"title": "x", "category": "course"}]
    n = analyze_headlines(items)
    assert n == 0
    assert "analysis" not in items[0]


def test_analyze_headlines_with_mocked_ollama(monkeypatch):
    monkeypatch.setattr(
        "src.tenant.tech_briefing.analyzer.settings.tech_briefing_llm_enabled",
        True,
    )
    monkeypatch.setattr(
        "src.tenant.tech_briefing.analyzer.settings.tech_briefing_llm_top_n", 3,
    )
    fake_json = (
        '{"what_it_is":"부트캠프 모집","who_benefits":"취준생",'
        '"recommendation":{"level":"APPLY","rationale":"마감 임박"},'
        '"action_tip":"신청 페이지 확인"}'
    )

    def fake_chat(system, user, **kwargs):
        return GenResult(text=fake_json, model="exaone3.5:7.8b",
                         eval_count=10, eval_duration_ms=100, ok=True)

    with patch("src.tenant.tech_briefing.analyzer.chat", side_effect=fake_chat):
        items = [
            {"title": "AI 부트캠프 모집", "summary": "", "category": "course",
             "is_recruiting": True},
            {"title": "AI 교육 뉴스", "summary": "", "category": "news"},
        ]
        n = analyze_headlines(items)

    # 프로파일 불요 — 모든 헤드라인 분석 대상.
    assert n == 2
    assert items[0]["analysis"]["recommendation"]["level"] == "APPLY"
    assert items[1]["analysis"]["what_it_is"] == "부트캠프 모집"


def test_analyze_headlines_respects_top_n(monkeypatch):
    monkeypatch.setattr(
        "src.tenant.tech_briefing.analyzer.settings.tech_briefing_llm_enabled",
        True,
    )
    monkeypatch.setattr(
        "src.tenant.tech_briefing.analyzer.settings.tech_briefing_llm_top_n", 1,
    )
    fake_json = '{"what_it_is":"소식","recommendation":{"level":"PLAN"}}'

    def fake_chat(system, user, **kwargs):
        return GenResult(text=fake_json, model="exaone3.5:7.8b",
                         eval_count=10, eval_duration_ms=100, ok=True)

    with patch("src.tenant.tech_briefing.analyzer.chat", side_effect=fake_chat):
        items = [
            {"title": "첫번째", "category": "policy"},
            {"title": "두번째", "category": "news"},
        ]
        n = analyze_headlines(items)

    assert n == 1
    assert items[0]["analysis"] is not None
    assert "analysis" not in items[1]     # top_n 밖 — 호출 안 함


def test_analyze_headlines_with_ollama_failure(monkeypatch):
    monkeypatch.setattr(
        "src.tenant.tech_briefing.analyzer.settings.tech_briefing_llm_enabled",
        True,
    )

    def fake_chat(system, user, **kwargs):
        return GenResult(text="", model="x", eval_count=0,
                         eval_duration_ms=0, ok=False, error="connection refused")

    with patch("src.tenant.tech_briefing.analyzer.chat", side_effect=fake_chat):
        items = [{"title": "AI 부트캠프", "category": "course"}]
        n = analyze_headlines(items)

    assert n == 0
    assert items[0]["analysis"] is None   # graceful fallback


def test_analyze_headlines_with_garbage_response(monkeypatch):
    monkeypatch.setattr(
        "src.tenant.tech_briefing.analyzer.settings.tech_briefing_llm_enabled",
        True,
    )

    def fake_chat(system, user, **kwargs):
        return GenResult(text="this is not json", model="x", eval_count=10,
                         eval_duration_ms=100, ok=True)

    with patch("src.tenant.tech_briefing.analyzer.chat", side_effect=fake_chat):
        items = [{"title": "AI 세미나", "category": "seminar"}]
        n = analyze_headlines(items)

    assert n == 0
    assert items[0]["analysis"] is None   # graceful fallback
