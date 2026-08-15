"""P2 설계 진화 회귀 테스트 (2026-08-15).

- Engagement: HMAC 서명·검증, 수집 엔드포인트(open/click/feedback), 개인화 주입
- 휴일 catch-up: 영업일 갭 판정
- PG 준비: article_key 기록·조회 (방언 중립 insert 경유)
- 릴레이 잡: 성공 시 마킹 / 실패 시 유지
- 콘텐츠 헬스 / 운영 경보 폴백
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.common import engagement
from src.common.database.repository import (
    init_db, get_session_factory,
    SubscriberRepository, SendHistoryRepository, SentArticleRepository,
)
from src.common.database.repo_engagement import EngagementEventRepository
from src.common.database.models import EngagementEvent
from src.config import settings

_SECRET = "test-secret-for-engagement"


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr(settings, "session_secret", _SECRET)
    monkeypatch.setattr(settings, "engagement_enabled", True)


@pytest.fixture
def client(tmp_path, secret):
    init_db(f"sqlite:///{tmp_path}/p2.db")
    from src.main import register_tenants
    register_tenants()
    from src.web.app import app
    return TestClient(app)


def _event_count(event_type=None):
    db = get_session_factory()()
    try:
        q = db.query(EngagementEvent)
        if event_type:
            q = q.filter(EngagementEvent.event_type == event_type)
        return q.count()
    finally:
        db.close()


# ──────────────────────────────────────────────────────────────
# Engagement — 토큰
# ──────────────────────────────────────────────────────────────

class TestEngagementToken:
    def test_sign_verify_roundtrip(self, secret):
        t = engagement.sign("o", "t1", 42)
        assert engagement.verify("o", "t1", 42, t)

    def test_tampered_rejected(self, secret):
        t = engagement.sign("o", "t1", 42)
        assert not engagement.verify("o", "t1", 43, t)      # 다른 구독자
        assert not engagement.verify("c", "t1", 42, t)      # 다른 종류
        assert not engagement.verify("o", "t1", 42, t[:-1] + "0")

    def test_disabled_without_secret(self, monkeypatch):
        monkeypatch.setattr(settings, "session_secret", "")
        assert engagement.make_open_pixel("t1", 1) == ""
        assert engagement.make_feedback_url("t1", 1, "up") == "#"
        assert not engagement.verify("o", "t1", 1, "anything")

    def test_click_url_binds_target(self, secret):
        url = "https://example.com/a?x=1"
        t = engagement.sign("c", "t1", 7, extra=url)
        assert engagement.verify("c", "t1", 7, t, extra=url)
        assert not engagement.verify("c", "t1", 7, t, extra="https://evil.com")


# ──────────────────────────────────────────────────────────────
# Engagement — 수집 엔드포인트
# ──────────────────────────────────────────────────────────────

class TestEngagementEndpoints:
    def test_open_records_and_returns_gif(self, client):
        t = engagement.sign("o", "allergy-insight", 11)
        r = client.get(f"/e/o/allergy-insight/11.gif?t={t}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/gif"
        assert _event_count("open") == 1

    def test_open_bad_sig_no_record(self, client):
        r = client.get("/e/o/allergy-insight/11.gif?t=bogus")
        assert r.status_code == 200  # 정보 미노출 — gif 는 항상 반환
        assert _event_count("open") == 0

    def test_click_redirects_and_records(self, client):
        url = "https://example.com/news?a=1"
        t = engagement.sign("c", "allergy-insight", 12, extra=url)
        from urllib.parse import quote
        r = client.get(
            f"/e/c/allergy-insight/12?u={quote(url, safe='')}&t={t}",
            follow_redirects=False,
        )
        assert r.status_code == 302
        assert r.headers["location"] == url
        assert _event_count("click") == 1

    def test_click_bad_sig_blocked(self, client):
        from urllib.parse import quote
        r = client.get(
            f"/e/c/allergy-insight/12?u={quote('https://evil.com', safe='')}&t=bad",
            follow_redirects=False,
        )
        assert r.status_code == 400  # open-redirect 차단
        assert _event_count("click") == 0

    def test_feedback_records(self, client):
        t = engagement.sign("f", "allergy-insight", 13, extra="up")
        r = client.get(f"/e/f/allergy-insight/13?v=up&t={t}")
        assert r.status_code == 200
        assert _event_count("feedback") == 1


# ──────────────────────────────────────────────────────────────
# Engagement — 개인화 주입
# ──────────────────────────────────────────────────────────────

class TestPersonalizeInjection:
    def test_pixel_and_feedback_injected(self, client):
        from src.common.scheduler.jobs import _personalize_html
        html = ("<body><a href='__FEEDBACK_UP_URL__'>u</a>"
                "<a href='__FEEDBACK_DOWN_URL__'>d</a></body>")
        out = _personalize_html(html, "allergy-insight", "tok", subscriber_id=5)
        assert "/e/o/allergy-insight/5.gif?t=" in out   # open pixel
        assert "/e/f/allergy-insight/5?v=up&t=" in out
        assert "/e/f/allergy-insight/5?v=down&t=" in out
        assert "__FEEDBACK" not in out

    def test_no_subscriber_neutralizes(self, client):
        from src.common.scheduler.jobs import _personalize_html
        out = _personalize_html(
            "<a href='__FEEDBACK_UP_URL__'>u</a>", "t1", "tok"
        )
        assert "href='#'" in out


# ──────────────────────────────────────────────────────────────
# 휴일 catch-up 갭 판정
# ──────────────────────────────────────────────────────────────

class TestCatchupGap:
    @pytest.fixture
    def session(self, tmp_path):
        init_db(f"sqlite:///{tmp_path}/catchup.db")
        sess = get_session_factory()()
        yield sess
        sess.close()

    def _seed_send(self, session, days_ago, send_mode="normal"):
        sub = SubscriberRepository.create(
            session, "t1", f"u{days_ago}@example.com", "n", f"tok-{days_ago}"
        )
        session.commit()
        h = SendHistoryRepository.create(
            session, "t1", sub.id, "제목", True, None,
            newsletter_type="daily", send_mode=send_mode,
        )
        h.sent_at = datetime.utcnow() - timedelta(days=days_ago)
        session.commit()

    def test_gap_after_holiday_skip(self, session, monkeypatch):
        from src.common.scheduler import jobs
        monkeypatch.setattr(jobs, "non_business_day_reason", lambda d=None: None)
        self._seed_send(session, days_ago=3)  # 금요일 발송 → 월요일 상황
        assert jobs._compute_catchup_days(session, "t1") == 3

    def test_no_gap_yesterday_sent(self, session, monkeypatch):
        from src.common.scheduler import jobs
        monkeypatch.setattr(jobs, "non_business_day_reason", lambda d=None: None)
        self._seed_send(session, days_ago=1)
        assert jobs._compute_catchup_days(session, "t1") is None

    def test_holiday_today_no_catchup(self, session, monkeypatch):
        from src.common.scheduler import jobs
        monkeypatch.setattr(
            jobs, "non_business_day_reason", lambda d=None: "weekend"
        )
        self._seed_send(session, days_ago=3)
        assert jobs._compute_catchup_days(session, "t1") is None

    def test_alert_history_ignored(self, session, monkeypatch):
        """stale alert 이력만 있으면 정식 발송 없음 → catch-up 미발동."""
        from src.common.scheduler import jobs
        monkeypatch.setattr(jobs, "non_business_day_reason", lambda d=None: None)
        self._seed_send(session, days_ago=3, send_mode="stale_admin_alert")
        assert jobs._compute_catchup_days(session, "t1") is None


# ──────────────────────────────────────────────────────────────
# PG 준비 — article_key
# ──────────────────────────────────────────────────────────────

class TestArticleKey:
    def test_record_backfills_key(self, tmp_path):
        init_db(f"sqlite:///{tmp_path}/ak.db")
        sess = get_session_factory()()
        try:
            from datetime import date
            n = SentArticleRepository.record_sent_articles(
                sess, "t1", date.today(),
                [(101, "http://x/a", "headline", "회사")],
            )
            sess.commit()
            assert n == 1
            keys = SentArticleRepository.list_recent_article_keys(sess, "t1")
            assert keys == ["101"]
            ids = SentArticleRepository.list_recent_article_ids(sess, "t1")
            assert ids == [101]
        finally:
            sess.close()


# ──────────────────────────────────────────────────────────────
# 릴레이 잡
# ──────────────────────────────────────────────────────────────

class TestEngagementRelay:
    def _seed_event(self, tenant="allergy-insight"):
        db = get_session_factory()()
        try:
            EngagementEventRepository.record(db, tenant, 1, "open")
            db.commit()
        finally:
            db.close()

    def test_relay_success_marks(self, client, monkeypatch):
        from src.common.scheduler import jobs
        from src.tenant.allergy_insight.config import tenant_settings
        monkeypatch.setattr(
            tenant_settings, "allergy_insight_newsletter_api_key", "k"
        )
        self._seed_event()
        monkeypatch.setattr(
            "httpx.post",
            lambda *a, **kw: SimpleNamespace(status_code=200),
        )
        jobs.run_engagement_relay_job("allergy-insight")
        db = get_session_factory()()
        try:
            assert EngagementEventRepository.list_unrelayed(
                db, "allergy-insight"
            ) == []
        finally:
            db.close()

    def test_relay_backend_missing_keeps_events(self, client, monkeypatch):
        from src.common.scheduler import jobs
        from src.tenant.allergy_insight.config import tenant_settings
        monkeypatch.setattr(
            tenant_settings, "allergy_insight_newsletter_api_key", "k"
        )
        self._seed_event()
        monkeypatch.setattr(
            "httpx.post",
            lambda *a, **kw: SimpleNamespace(status_code=404),
        )
        jobs.run_engagement_relay_job("allergy-insight")
        db = get_session_factory()()
        try:
            assert len(EngagementEventRepository.list_unrelayed(
                db, "allergy-insight"
            )) == 1  # 미마킹 — 다음 실행에서 재시도
        finally:
            db.close()


# ──────────────────────────────────────────────────────────────
# 콘텐츠 헬스 / 경보
# ──────────────────────────────────────────────────────────────

class TestContentHealth:
    def test_endpoint_structure(self, client):
        r = client.get("/api/health/content")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] in ("ok", "warn")
        assert "allergy-insight" in body["tenants"]
        assert "tech-briefing" in body["tenants"]

    def test_liveness_contract_untouched(self, client):
        """QA Agent 계약 — /api/health 는 여전히 {status} liveness."""
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_ops_alert_disabled_without_webhook(monkeypatch):
    from src.common.alerts import send_ops_alert
    monkeypatch.setattr(settings, "ops_slack_webhook_url", "")
    assert send_ops_alert("테스트") is False  # HTTP 미발생, 예외 없음
