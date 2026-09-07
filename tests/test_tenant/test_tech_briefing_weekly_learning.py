"""weekly 브리핑 '이번 주 학습' 블록 — SkillRadar /public/stats.learning (Phase 3 N4)."""
from datetime import datetime

from jinja2 import ChainableUndefined, Environment, FileSystemLoader


def _render(stats):
    # 블록 단위 테스트 — 나머지 컨텍스트는 비어 있어도 렌더되게 ChainableUndefined
    env = Environment(loader=FileSystemLoader("templates"), undefined=ChainableUndefined)
    tpl = env.get_template("tech_briefing/weekly_report.html")
    now = datetime.now()
    return tpl.render(
        report_date=now, generated_at=now, week_start=now, week_end=now, headlines=[], recruiting=[], keywords=[],
        daily_trend=[], trend_max=0, skillradar_stats=stats, stats={}, footer_extras={"recruiting": [], "keywords": []},
        weekly_headlines=[], weekly_recruiting=[], weekly_keywords=[], history_total=0, wow={},
    )


def test_learning_block_disclosed():
    html = _render({"resources_total": 10, "learning": {
        "week_from": "2026-09-01", "week_to": "2026-09-07", "lessons_published": 8, "disclosed": True,
        "active_learners": 6, "week_completion_rate": 0.75, "quiz_avg": 0.8,
        "popular_themes": [{"theme_title": "AI 도구 지도"}, {"theme_title": "첫 대화"}]}})
    assert "이번 주 학습" in html and "완료율 75%" in html and "퀴즈 평균 80점" in html and "AI 도구 지도 · 첫 대화" in html


def test_learning_block_hidden_when_small_or_missing():
    html = _render({"resources_total": 10, "learning": {
        "week_from": "2026-09-01", "week_to": "2026-09-07", "lessons_published": 8, "disclosed": False,
        "active_learners": None, "week_completion_rate": None, "quiz_avg": None, "popular_themes": []}})
    assert "발행 레슨 8개" in html and "완료율" not in html
    assert "이번 주 학습" not in _render({"resources_total": 10})
    assert "이번 주 학습" not in _render({"resources_total": 10, "learning": None})
