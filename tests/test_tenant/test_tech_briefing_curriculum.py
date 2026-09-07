"""TechBriefing '오늘의 학습' 섹션 — SkillRadar 커리큘럼 공급 컨텍스트 (Phase 1).

#1 정상 payload → 트랙별 레슨·딥링크·소요분
#2 fallback / 빈 tracks / failed 트랙 → None (섹션 생략)
#3 정답·해설이 섞여 와도 컨텍스트에 실리지 않음 (제목·링크만)
#4 formatter.format 통합 — tech_daily.curriculum 있으면 컨텍스트 키 채움, 없으면 None
"""

from datetime import datetime, timezone

import pytest

from src.tenant.tech_briefing.config import tenant_settings as settings
from src.tenant.tech_briefing.curriculum import build_curriculum_context
from src.tenant.tech_briefing.formatter import TechBriefingFormatter


@pytest.fixture(autouse=True)
def _disable_llm():
    orig = settings.tech_briefing_llm_enabled
    settings.tech_briefing_llm_enabled = False
    yield
    settings.tech_briefing_llm_enabled = orig


def _payload(status="ok"):
    link = "https://skillradar.unmong.com/learn?date=2026-10-05&lesson={}&ch=email"
    return {
        "date": "2026-10-05", "fallback": False,
        "tracks": [{
            "track": "senior", "status": status, "theme_code": "B1", "theme_title": "AI 가 바꾸는 내 업무",
            "focus": "산업별 적용 사례", "cohort_week": 1,
            "lessons": [
                {"id": 41, "kind": "read", "title": "산업별 AI 적용 사례", "est_min": 6, "link": link.format(41),
                 "body_md": "본문…", "key_points": ["a", "b", "c"]},
                {"id": 42, "kind": "practice", "title": "내 업무 후보 고르기", "est_min": 5, "link": link.format(42),
                 "prompt_text": "…"},
                {"id": 43, "kind": "quiz", "title": "확인 퀴즈", "est_min": 2, "link": link.format(43), "n_questions": 3},
                {"id": 44, "kind": "trend", "title": "오늘의 동향", "est_min": 1, "link": link.format(44), "items": [{}, {}]},
            ],
        }, {
            "track": "newcomer", "status": "failed", "theme_code": "A1", "theme_title": "x", "lessons": [],
        }],
    }


def test_context_tracks_and_lessons():
    ctx = build_curriculum_context(_payload())
    assert ctx["date"] == "2026-10-05" and len(ctx["tracks"]) == 1  # failed 트랙 제외
    t = ctx["tracks"][0]
    assert t["label"] == "시니어 트랙" and t["week"] == 1 and t["total_min"] == 14 and t["partial"] is False
    assert [l["kind_label"] for l in t["lessons"]] == ["읽기", "실습", "확인 퀴즈", "동향"]
    assert t["lessons"][2]["extra"] == "3문항" and t["lessons"][3]["extra"] == "2건"
    assert t["link"].endswith("lesson=41&ch=email")


def test_partial_flag_on_fallback_status():
    assert build_curriculum_context(_payload(status="fallback"))["tracks"][0]["partial"] is True


def test_none_when_missing_or_fallback():
    assert build_curriculum_context(None) is None
    assert build_curriculum_context({"date": "2026-10-05", "fallback": True, "tracks": []}) is None
    p = _payload(); p["tracks"][0]["lessons"] = []
    assert build_curriculum_context(p) is None
    p = _payload(); p["tracks"][0]["lessons"] = [{"kind": "read", "title": "링크 없음", "est_min": 5}]
    assert build_curriculum_context(p) is None


def test_no_body_or_answers_leak():
    p = _payload()
    p["tracks"][0]["lessons"][2]["questions"] = [{"q": "?", "answer_index": 1, "explanation": "정답 해설"}]
    ctx = build_curriculum_context(p)
    flat = repr(ctx)
    assert "본문…" not in flat and "answer_index" not in flat and "정답 해설" not in flat and "prompt_text" not in flat


def test_formatter_integration():
    f = TechBriefingFormatter()
    base = {"report_date": datetime.now(timezone.utc).isoformat(),
            "news_items": [{"source": "news", "category": "news", "title": "뉴스", "url": "https://n.example/1",
                            "published_at": datetime.now(timezone.utc), "summary": "", "is_recruiting": False,
                            "dedup_key": "news:1", "origin": "구글뉴스", "keyword": "AI"}],
            "policy_items": [], "course_items": []}
    ctx = f.format({"tech_daily": {**base, "curriculum": _payload()}})
    assert ctx["curriculum"]["tracks"][0]["theme_title"] == "AI 가 바꾸는 내 업무"
    ctx = f.format({"tech_daily": base})
    assert ctx["curriculum"] is None
