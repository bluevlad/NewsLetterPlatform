"""TechBriefing analyzer + ollama_client + prompts 단위 테스트 (Ollama mock)."""

from unittest.mock import patch

from src.tenant.tech_briefing.analyzer import (
    _normalize_analysis,
    _pick_profile_for_item,
    analyze_headlines,
)
from src.tenant.tech_briefing.ollama_client import GenResult, parse_json_response
from src.tenant.tech_briefing.prompts import render_user_prompt
from src.tenant.tech_briefing.service_profiles import (
    KnownDebt,
    ServiceProfile,
    reset_cache,
)


def _profile() -> ServiceProfile:
    return ServiceProfile(
        service="hopenvision",
        stack_summary="Java 17 + Spring Boot 3.2 + React 19",
        high_interest=("Spring Boot 3", "React 19"),
        low_interest=("Android",),
        known_debt=(KnownDebt(area="identity 도메인", state="Sprint 0 revert"),),
    )


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
        "what_changed": "변경 요약",
        "service_impact": "영향",
        "recommendation": {"level": "ADOPT", "rationale": "이유"},
        "estimated_cost": "비용",
    }
    out = _normalize_analysis(raw)
    assert out["recommendation"]["level"] == "ADOPT"
    assert out["what_changed"] == "변경 요약"


def test_normalize_invalid_level_defaults_to_assess():
    raw = {
        "what_changed": "X",
        "recommendation": {"level": "URGENT", "rationale": "..."},
    }
    out = _normalize_analysis(raw)
    assert out["recommendation"]["level"] == "ASSESS"


def test_normalize_missing_what_changed_returns_none():
    raw = {"service_impact": "..."}
    assert _normalize_analysis(raw) is None


def test_normalize_non_dict_returns_none():
    assert _normalize_analysis(None) is None
    assert _normalize_analysis("string") is None


def test_pick_profile_picks_highest_relevance():
    profiles = [_profile()]
    item = {"service_relevance": {"hopenvision": {"score": 3.5}}}
    picked = _pick_profile_for_item(item, profiles)
    assert picked is not None
    assert picked.service == "hopenvision"


def test_pick_profile_returns_none_when_score_zero():
    profiles = [_profile()]
    item = {"service_relevance": {"hopenvision": {"score": 0}}}
    assert _pick_profile_for_item(item, profiles) is None


def test_pick_profile_returns_none_when_no_relevance():
    profiles = [_profile()]
    item = {}
    assert _pick_profile_for_item(item, profiles) is None


def test_render_user_prompt_includes_known_debt():
    item = {
        "source": "github_release", "project": "spring-boot",
        "ecosystem": "java-be", "tier": "S",
        "title": "Spring Boot 3.3", "url": "https://x",
        "summary": "release notes", "published_at": "2026-05-18",
    }
    text = render_user_prompt(
        service="hopenvision",
        stack_summary="J17 + SB3",
        high_signals=["Spring Boot 3"],
        known_debt=[{"area": "identity 도메인", "state": "revert"}],
        item=item,
    )
    assert "hopenvision" in text
    assert "Spring Boot 3" in text
    assert "identity 도메인" in text
    assert "J17 + SB3" in text


def test_render_user_prompt_cve_block():
    item = {
        "source": "nvd_cve", "project": "spring framework",
        "ecosystem": "java-be", "tier": "S",
        "title": "CVE-2026-X", "url": "https://nvd",
        "cvss": 9.5, "severity": "critical",
        "summary": "RCE", "published_at": "2026-05-18",
    }
    text = render_user_prompt(
        service="hopenvision", stack_summary="",
        high_signals=[], known_debt=[], item=item,
    )
    assert "cvss: 9.5 CRITICAL" in text


def test_analyze_headlines_skips_when_disabled(monkeypatch):
    monkeypatch.setattr(
        "src.tenant.tech_briefing.analyzer.settings.tech_briefing_llm_enabled",
        False,
    )
    items = [{"title": "x", "service_relevance": {"hopenvision": {"score": 4}}}]
    n = analyze_headlines(items)
    assert n == 0
    assert "analysis" not in items[0]


def test_analyze_headlines_with_mocked_ollama(monkeypatch):
    reset_cache()
    monkeypatch.setattr(
        "src.tenant.tech_briefing.analyzer.settings.tech_briefing_llm_enabled",
        True,
    )
    monkeypatch.setattr(
        "src.tenant.tech_briefing.analyzer.settings.tech_briefing_llm_top_n", 3,
    )
    fake_json = (
        '{"what_changed":"변경","service_impact":"영향",'
        '"recommendation":{"level":"ADOPT","rationale":"이유"},'
        '"estimated_cost":"비용"}'
    )

    def fake_chat(system, user, **kwargs):
        return GenResult(text=fake_json, model="qwen2.5-coder:14b",
                         eval_count=10, eval_duration_ms=100, ok=True)

    with patch("src.tenant.tech_briefing.analyzer.chat", side_effect=fake_chat):
        items = [
            {"title": "Spring Boot 3.3 GA", "summary": "",
             "service_relevance": {"hopenvision": {"score": 4.0}}},
            {"title": "irrelevant",
             "service_relevance": {"hopenvision": {"score": 0}}},  # skipped
        ]
        n = analyze_headlines(items)

    assert n == 1
    assert items[0]["analysis"]["recommendation"]["level"] == "ADOPT"
    assert items[1].get("analysis") is None  # zero score → skipped


def test_analyze_headlines_with_ollama_failure(monkeypatch):
    reset_cache()
    monkeypatch.setattr(
        "src.tenant.tech_briefing.analyzer.settings.tech_briefing_llm_enabled",
        True,
    )

    def fake_chat(system, user, **kwargs):
        return GenResult(text="", model="x", eval_count=0,
                         eval_duration_ms=0, ok=False, error="connection refused")

    with patch("src.tenant.tech_briefing.analyzer.chat", side_effect=fake_chat):
        items = [
            {"title": "Spring Boot 3.3",
             "service_relevance": {"hopenvision": {"score": 4.0}}},
        ]
        n = analyze_headlines(items)

    assert n == 0
    assert items[0]["analysis"] is None   # graceful fallback


def test_analyze_headlines_with_garbage_response(monkeypatch):
    reset_cache()
    monkeypatch.setattr(
        "src.tenant.tech_briefing.analyzer.settings.tech_briefing_llm_enabled",
        True,
    )

    def fake_chat(system, user, **kwargs):
        return GenResult(text="this is not json", model="x", eval_count=10,
                         eval_duration_ms=100, ok=True)

    with patch("src.tenant.tech_briefing.analyzer.chat", side_effect=fake_chat):
        items = [
            {"title": "Spring Boot 3.3",
             "service_relevance": {"hopenvision": {"score": 4.0}}},
        ]
        n = analyze_headlines(items)

    assert n == 0
    assert items[0]["analysis"] is None   # graceful fallback
