"""P0 하드닝 회귀 테스트 (2026-08-15 전체 설계 진단 후속).

- C1  관리자 허용목록 fail-close (빈 SUPER_ADMIN_EMAILS → 전체 거부)
- H1  SQLite WAL + busy_timeout PRAGMA 적용
- H2  adhoc dedup 이 제목까지 매칭 (같은 날 두 번째 adhoc 오스킵 방지)
- M15 이메일 형식 검증
- H5  스케줄러 heartbeat 기반 healthcheck 판정
"""

import json
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.common.database.repository import (
    init_db,
    get_session_factory,
    SubscriberRepository,
    SendHistoryRepository,
)
from src.common.scheduler import health
from src.common.security import is_valid_email
from src.web.admin import auth as admin_auth


# ──────────────────────────────────────────────────────────────
# C1 — 관리자 허용목록 fail-close
# ──────────────────────────────────────────────────────────────

class TestAdminAllowlistFailClose:
    def _client(self, monkeypatch, allowlist: str, verified_email: str):
        monkeypatch.setattr(admin_auth.settings, "google_client_id", "test-client")
        monkeypatch.setattr(admin_auth.settings, "super_admin_emails", allowlist)
        monkeypatch.setattr(
            admin_auth, "_verify_google_id_token",
            lambda credential: {"email": verified_email},
        )
        app = FastAPI()
        app.include_router(admin_auth.router)
        return TestClient(app)

    def test_empty_allowlist_rejects_any_account(self, monkeypatch):
        """빈 SUPER_ADMIN_EMAILS — 검증된 Google 계정도 거부 (fail-close)."""
        client = self._client(monkeypatch, "", "anyone@example.com")
        r = client.post("/admin/auth/google/verify", json={"credential": "x"})
        assert r.status_code == 403

    def test_listed_email_accepted(self, monkeypatch):
        client = self._client(monkeypatch, "admin@example.com", "admin@example.com")
        r = client.post("/admin/auth/google/verify", json={"credential": "x"})
        assert r.status_code == 200
        assert "admin_session" in r.cookies

    def test_unlisted_email_rejected(self, monkeypatch):
        client = self._client(monkeypatch, "admin@example.com", "intruder@example.com")
        r = client.post("/admin/auth/google/verify", json={"credential": "x"})
        assert r.status_code == 403


# ──────────────────────────────────────────────────────────────
# H1 — SQLite WAL PRAGMA
# ──────────────────────────────────────────────────────────────

def test_sqlite_wal_and_busy_timeout(tmp_path):
    init_db(f"sqlite:///{tmp_path}/wal.db")
    sess = get_session_factory()()
    try:
        assert sess.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert sess.execute(text("PRAGMA busy_timeout")).scalar() == 15000
    finally:
        sess.close()


# ──────────────────────────────────────────────────────────────
# H2 — adhoc dedup 제목 매칭
# ──────────────────────────────────────────────────────────────

class TestAdhocDedupBySubject:
    @pytest.fixture
    def session(self, tmp_path):
        init_db(f"sqlite:///{tmp_path}/adhoc.db")
        sess = get_session_factory()()
        yield sess
        sess.close()

    def test_second_adhoc_with_new_subject_not_skipped(self, session):
        sub = SubscriberRepository.create(
            session, "t1", "user@example.com", "이름", "tok-1"
        )
        session.commit()
        SendHistoryRepository.create(
            session, "t1", sub.id, "첫 번째 공지", True, None,
            newsletter_type="adhoc",
        )
        session.commit()

        same = SendHistoryRepository.get_sent_today_subscriber_ids(
            session, "t1", newsletter_type="adhoc", subject="첫 번째 공지"
        )
        other = SendHistoryRepository.get_sent_today_subscriber_ids(
            session, "t1", newsletter_type="adhoc", subject="두 번째 공지"
        )
        any_subject = SendHistoryRepository.get_sent_today_subscriber_ids(
            session, "t1", newsletter_type="adhoc"
        )
        assert sub.id in same        # 같은 제목 → 중복 방지 유지
        assert sub.id not in other   # 다른 제목 → 발송 대상 (기존 버그: 스킵됨)
        assert sub.id in any_subject # subject 미지정 호출은 기존 동작 보존


# ──────────────────────────────────────────────────────────────
# M15 — 이메일 형식 검증
# ──────────────────────────────────────────────────────────────

class TestEmailValidation:
    def test_valid(self):
        for e in ("a@b.co", "user.name+tag@example.com", "x_1@sub.domain.org"):
            assert is_valid_email(e), e

    def test_invalid(self):
        for e in ("", "notanemail", "a@b", "a @b.com", "a@b..com", "a@.com",
                  "@example.com", "user@", "user@example.com\n", "a" * 250 + "@b.co"):
            assert not is_valid_email(e), repr(e)


# ──────────────────────────────────────────────────────────────
# H5 — heartbeat healthcheck
# ──────────────────────────────────────────────────────────────

class TestHeartbeat:
    def test_fresh_heartbeat_healthy(self, tmp_path, monkeypatch):
        f = tmp_path / ".scheduler_health"
        f.write_text(json.dumps({"heartbeat": datetime.utcnow().isoformat()}))
        monkeypatch.setattr(health, "HEALTH_FILE", f)
        assert health.check_heartbeat() is True

    def test_stale_heartbeat_unhealthy(self, tmp_path, monkeypatch):
        stale = (datetime.utcnow() - timedelta(hours=2)).isoformat()
        f = tmp_path / ".scheduler_health"
        f.write_text(json.dumps({"heartbeat": stale}))
        monkeypatch.setattr(health, "HEALTH_FILE", f)
        assert health.check_heartbeat() is False

    def test_missing_file_is_startup_grace(self, tmp_path, monkeypatch):
        monkeypatch.setattr(health, "HEALTH_FILE", tmp_path / "absent")
        assert health.check_heartbeat() is True
