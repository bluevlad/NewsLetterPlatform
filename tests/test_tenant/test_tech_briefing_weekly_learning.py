"""weekly 브리핑 '이번 주 학습' 블록 — SkillRadar /public/stats.learning (Phase 3 N4).

formatter.format_weekly 로 실제 컨텍스트를 만들고 템플릿을 렌더해 블록 유무를 검증한다.
"""
from datetime import date, timedelta

from jinja2 import Environment, FileSystemLoader

from src.tenant.tech_briefing.formatter import TechBriefingFormatter
from tests.test_tenant.test_tech_briefing_weekly_dedup import _history_row

MONDAY = date(2026, 9, 7)


def _render(learning):
    history = [_history_row(MONDAY + timedelta(days=d)) for d in range(5)]
    stats = {"resources_total": 10, "last_ingest_at": "2026-09-07T03:10:00"}
    if learning is not None:
        stats["learning"] = learning
    ctx = TechBriefingFormatter().format_weekly(history, {"tech_weekly": {"skillradar_stats": stats}})
    env = Environment(loader=FileSystemLoader("templates"))
    return env.get_template("tech_briefing/weekly_report.html").render(**ctx)


def test_learning_block_disclosed():
    html = _render({
        "week_from": "2026-09-01", "week_to": "2026-09-07", "lessons_published": 8, "disclosed": True,
        "active_learners": 6, "week_completion_rate": 0.75, "quiz_avg": 0.8,
        "popular_themes": [{"theme_title": "AI 도구 지도"}, {"theme_title": "첫 대화"}]})
    assert "이번 주 학습" in html and "완료율 75%" in html and "퀴즈 평균 80점" in html and "AI 도구 지도 · 첫 대화" in html


def test_learning_block_hidden_when_small_or_missing():
    html = _render({
        "week_from": "2026-09-01", "week_to": "2026-09-07", "lessons_published": 8, "disclosed": False,
        "active_learners": None, "week_completion_rate": None, "quiz_avg": None, "popular_themes": []})
    assert "발행 레슨 8개" in html and "완료율" not in html
    assert "이번 주 학습" not in _render(None)
