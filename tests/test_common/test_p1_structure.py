"""P1 구조 리팩토링 회귀 테스트 (2026-08-15 설계 진단 후속).

- SendPlan 생성자별 정책 필드 (SEND_TYPE_SEPARATION 표준 준수)
- dedup 조회의 send_mode='normal' 필터 (M1)
- _personalize_html 공통 치환 (M2 드리프트 방지)
- 스케줄러 frequency 게이트 — weekly 전용 테넌트에 daily 잡 미등록 (M12)
- 페르소나 카탈로그 테넌트 게이트 (M11)
"""

import asyncio
from types import SimpleNamespace

import pytest

from src.common.database.repository import (
    init_db,
    get_session_factory,
    SubscriberRepository,
    SendHistoryRepository,
)
from src.common.scheduler.jobs import _personalize_html
from src.common.scheduler.send_plan import (
    SendPlan, alert_plan, holiday_test_plan, manual_plan, normal_plan,
)


class TestSendPlan:
    def test_normal(self):
        p = normal_plan("daily")
        assert (p.mode, p.history_type, p.send_mode) == ("normal", "daily", "normal")
        assert p.dedup and p.archive and p.record_articles
        assert not p.admin_only

    def test_manual_isolated_axis(self):
        """manual 은 newsletter_type 축을 'manual' 로 격리 (3/21 dedup 오염 사고 방지)."""
        p = manual_plan("daily")
        assert p.history_type == "manual"
        assert p.send_mode == "normal"
        assert not (p.dedup or p.archive or p.record_articles or p.admin_only)

    def test_holiday_weekend_vs_holiday(self):
        w = holiday_test_plan("daily", "weekend")
        h = holiday_test_plan("daily", "holiday")
        assert w.send_mode == "weekend_test" and h.send_mode == "holiday_test"
        for p in (w, h):
            assert p.admin_only
            assert p.history_type == "daily"  # 콘텐츠 축은 유지
            assert not (p.dedup or p.archive or p.record_articles)

    def test_alert_overrides_normal(self):
        p = alert_plan(normal_plan("daily"), "stale_admin_alert")
        assert p.mode == "stale_admin_alert" and p.send_mode == "stale_admin_alert"
        assert p.history_type == "daily"
        assert p.admin_only
        assert not (p.dedup or p.archive or p.record_articles)


class TestDedupSendModeFilter:
    """M1: 경보/테스트 발송 이력(newsletter_type='daily')이 dedup 을 오염시키지 않는다."""

    @pytest.fixture
    def session(self, tmp_path):
        init_db(f"sqlite:///{tmp_path}/dedup.db")
        sess = get_session_factory()()
        yield sess
        sess.close()

    def test_alert_history_not_deduped(self, session):
        sub = SubscriberRepository.create(
            session, "t1", "admin@example.com", "관리자", "tok-a"
        )
        session.commit()
        # stale alert 가 daily 타입으로 기록된 상황 (관리자=구독자 케이스)
        SendHistoryRepository.create(
            session, "t1", sub.id, "제목", True, None,
            newsletter_type="daily", send_mode="stale_admin_alert",
        )
        session.commit()

        sent = SendHistoryRepository.get_sent_today_subscriber_ids(
            session, "t1", newsletter_type="daily"
        )
        assert sub.id not in sent  # 이후 정상 발송에서 스킵되면 안 됨

        # 정식 발송 기록은 dedup 에 잡혀야 함
        SendHistoryRepository.create(
            session, "t1", sub.id, "제목", True, None,
            newsletter_type="daily", send_mode="normal",
        )
        session.commit()
        sent = SendHistoryRepository.get_sent_today_subscriber_ids(
            session, "t1", newsletter_type="daily"
        )
        assert sub.id in sent


class TestPersonalizeHtml:
    def test_replaces_both_placeholders(self):
        html = "<a href='__UNSUBSCRIBE_URL__'>u</a><a href='__PERSONA_REQUEST_URL__'>p</a>"
        out = _personalize_html(html, "allergy-insight", "tok-123")
        assert "__UNSUBSCRIBE_URL__" not in out
        assert "__PERSONA_REQUEST_URL__" not in out
        assert "/allergy-insight/unsubscribe/token/tok-123" in out
        assert "/allergy-insight/persona/request?token=tok-123" in out

    def test_noop_without_placeholders(self):
        assert _personalize_html("<p>hi</p>", "t", "tok") == "<p>hi</p>"


class TestSchedulerFrequencyGate:
    """M12: daily 는 supported_frequencies 로 게이트.

    standup 테넌트는 2026-08-15 legacy 이동 — 잡 자체가 등록되지 않아야 한다.
    """

    def test_registered_jobs(self):
        from apscheduler.schedulers.background import BackgroundScheduler
        from src.main import register_tenants
        from src.common.scheduler import jobs

        register_tenants()
        sch = BackgroundScheduler()
        jobs.register_all_jobs(sch)
        ids = {j.id for j in sch.get_jobs()}

        # legacy 이동한 standup 잡은 일절 없음
        assert not any("standup" in i for i in ids)
        # daily 지원 테넌트는 기존 id 체계 그대로 유지
        assert "collect_allergy-insight" in ids
        assert "send_allergy-insight_early" in ids
        assert "collect_monthly_allergy-insight" in ids


class TestPersonaTenantGate:
    """M11: persona_enabled=False 테넌트는 카탈로그 조회조차 하지 않는다."""

    def test_disabled_tenant_returns_empty_without_client_call(self, monkeypatch):
        from src.web import app as web_app

        def _boom():
            raise AssertionError("disabled 테넌트에서 카탈로그 호출 금지")

        monkeypatch.setattr(
            web_app.persona_client, "get_personas", _boom
        )
        result = asyncio.run(
            web_app._personas_for(SimpleNamespace(persona_enabled=False))
        )
        assert result == []

    def test_enabled_tenant_fetches_catalog(self, monkeypatch):
        from src.web import app as web_app

        async def _fake():
            return [{"code": "patient"}]

        monkeypatch.setattr(web_app.persona_client, "get_personas", _fake)
        result = asyncio.run(
            web_app._personas_for(SimpleNamespace(persona_enabled=True))
        )
        assert result == [{"code": "patient"}]

    def test_tenant_defaults(self):
        from src.main import register_tenants
        from src.tenant.registry import get_registry

        register_tenants()
        reg = get_registry()
        assert reg.get("allergy-insight").persona_enabled is True
        assert reg.get("tech-briefing").persona_enabled is False
