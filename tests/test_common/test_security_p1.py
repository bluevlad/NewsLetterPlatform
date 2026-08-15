"""보안 P1 회귀 테스트 (2026-08-15 프로젝트 검증 보고서 후속 — fix/security-p1).

- SEC-02  GET 구독 해지 링크는 상태 변경 없음 (확인 페이지) + POST 로 해지 실행
          + RFC 8058 one-click POST + 기존 발송 메일 링크(같은 URL) 호환
- SEC-02  발송 메일에 List-Unsubscribe / List-Unsubscribe-Post 헤더 포함
- SEC-03  관리자 비밀번호 로그인 rate limit (윈도 내 실패 초과 → 429)
- SEC-03  세션 쿠키 Secure 플래그 — https base URL 에서만 적용
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.common.database.repository import (
    init_db,
    get_session_factory,
    SubscriberRepository,
)
from src.common.scheduler.jobs import _unsubscribe_headers, _unsubscribe_url
from src.web.admin import auth as admin_auth


# ──────────────────────────────────────────────────────────────
# SEC-02 — GET 해지 부작용 제거 + POST 해지
# ──────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path):
    """임시 DB + 테넌트 등록 후 FastAPI TestClient."""
    init_db(f"sqlite:///{tmp_path}/sec.db")
    from src.main import register_tenants
    register_tenants()
    from src.web.app import app
    return TestClient(app)


def _seed_subscriber(token="tok-sec", email="sec@example.com"):
    sess = get_session_factory()()
    try:
        sub = SubscriberRepository.create(
            sess, "allergy-insight", email, "보안테스트", token,
        )
        sess.commit()
        return sub.id
    finally:
        sess.close()


def _is_active(token):
    sess = get_session_factory()()
    try:
        sub = SubscriberRepository.get_by_unsubscribe_token(sess, token)
        return bool(sub and sub.is_active)
    finally:
        sess.close()


class TestUnsubscribeGetNoSideEffect:
    def test_get_renders_confirm_without_state_change(self, client):
        """GET 링크 방문(스캐너/프리페처 포함)은 구독 상태를 바꾸지 않는다."""
        _seed_subscriber(token="tok-get")
        resp = client.get("/allergy-insight/unsubscribe/token/tok-get")
        assert resp.status_code == 200
        assert "구독을 해지하시겠습니까" in resp.text
        assert _is_active("tok-get") is True

        # 반복 방문(스캐너 재방문 시나리오)에도 여전히 활성
        client.get("/allergy-insight/unsubscribe/token/tok-get")
        assert _is_active("tok-get") is True

    def test_get_invalid_token_shows_error(self, client):
        resp = client.get("/allergy-insight/unsubscribe/token/no-such-token")
        assert resp.status_code == 200
        assert "유효하지 않은 링크" in resp.text

    def test_post_performs_unsubscribe(self, client):
        """확인 페이지 form POST → 실제 해지 (기존 메일 링크와 같은 URL)."""
        _seed_subscriber(token="tok-post", email="post@example.com")
        resp = client.post("/allergy-insight/unsubscribe/token/tok-post")
        assert resp.status_code == 200
        assert "구독이 해지되었습니다" in resp.text
        assert _is_active("tok-post") is False

    def test_one_click_post_rfc8058(self, client):
        """RFC 8058: 메일 제공자의 List-Unsubscribe=One-Click POST 도 해지된다."""
        _seed_subscriber(token="tok-oneclick", email="oneclick@example.com")
        resp = client.post(
            "/allergy-insight/unsubscribe/token/tok-oneclick",
            data={"List-Unsubscribe": "One-Click"},
        )
        assert resp.status_code == 200
        assert _is_active("tok-oneclick") is False

    def test_post_invalid_token_no_crash(self, client):
        resp = client.post("/allergy-insight/unsubscribe/token/no-such-token")
        assert resp.status_code == 200
        assert "유효하지 않은 링크" in resp.text


def test_unsubscribe_headers_rfc8058():
    """발송 헤더 — List-Unsubscribe(URL) + One-Click POST 선언."""
    headers = _unsubscribe_headers("allergy-insight", "tok-h")
    assert headers["List-Unsubscribe"] == f"<{_unsubscribe_url('allergy-insight', 'tok-h')}>"
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert "/allergy-insight/unsubscribe/token/tok-h" in headers["List-Unsubscribe"]


# ──────────────────────────────────────────────────────────────
# SEC-03 — 관리자 로그인 rate limit + Secure 쿠키
# ──────────────────────────────────────────────────────────────

class TestAdminLoginRateLimit:
    def _client(self, monkeypatch, password="correct-pw"):
        monkeypatch.setattr(admin_auth.settings, "admin_password", password)
        admin_auth._reset_login_rate_limit()
        app = FastAPI()
        app.include_router(admin_auth.router)
        return TestClient(app)

    def test_lockout_after_max_failures(self, monkeypatch):
        """윈도 내 실패 한도 초과 → 429 (정답을 넣어도 잠금 유지)."""
        client = self._client(monkeypatch)
        for _ in range(admin_auth.LOGIN_MAX_FAILURES):
            r = client.post("/admin/login", data={"password": "wrong"})
            assert r.status_code == 200  # 실패 페이지
        r = client.post("/admin/login", data={"password": "wrong"})
        assert r.status_code == 429
        # 잠금 중에는 올바른 비밀번호도 거부
        r = client.post("/admin/login", data={"password": "correct-pw"})
        assert r.status_code == 429
        admin_auth._reset_login_rate_limit()

    def test_success_clears_failures(self, monkeypatch):
        client = self._client(monkeypatch)
        for _ in range(admin_auth.LOGIN_MAX_FAILURES - 1):
            client.post("/admin/login", data={"password": "wrong"})
        r = client.post("/admin/login", data={"password": "correct-pw"}, follow_redirects=False)
        assert r.status_code == 303
        assert "admin_session" in r.cookies
        # 성공으로 실패 카운터 초기화 — 다시 한도만큼 시도 가능
        for _ in range(admin_auth.LOGIN_MAX_FAILURES):
            r = client.post("/admin/login", data={"password": "wrong"})
            assert r.status_code == 200
        admin_auth._reset_login_rate_limit()


class TestSessionCookieSecure:
    def _login(self, monkeypatch, base_url):
        monkeypatch.setattr(admin_auth.settings, "admin_password", "pw")
        monkeypatch.setattr(admin_auth.settings, "web_base_url", base_url)
        admin_auth._reset_login_rate_limit()
        app = FastAPI()
        app.include_router(admin_auth.router)
        client = TestClient(app)
        return client.post("/admin/login", data={"password": "pw"}, follow_redirects=False)

    def test_secure_flag_on_https(self, monkeypatch):
        r = self._login(monkeypatch, "https://newsletter.example.com")
        set_cookie = r.headers["set-cookie"]
        assert "Secure" in set_cookie
        assert "HttpOnly" in set_cookie

    def test_no_secure_flag_on_local_http(self, monkeypatch):
        r = self._login(monkeypatch, "http://localhost:4050")
        set_cookie = r.headers["set-cookie"]
        assert "Secure" not in set_cookie
        assert "HttpOnly" in set_cookie
