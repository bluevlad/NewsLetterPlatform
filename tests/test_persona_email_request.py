"""페르소나 적응형 뉴스레터 — 이메일 콘텐츠 요청 (E1·E2) 테스트.

E-T1  GET 랜딩 — 유효 토큰 → 폼 렌더, 부작용 없음 (미러 행 미생성)
E-T2  GET 랜딩 — section 파라미터 → 해당 분야 컨텍스트
E-T3  GET 랜딩 — 무효 토큰 → 에러 페이지
E-T4  topic-request POST section → build_topic_request context.section 전달
E-T5  daily_report.html — persona_enabled 가드 (CTA·딥링크 노출/숨김)
E-T6  formatter — persona_enabled 컨텍스트 키
"""

import pytest
from fastapi.testclient import TestClient

from src.common.database.repository import (
    init_db,
    get_session_factory,
    SubscriberRepository,
    SubscriberTopicRequestRepository,
)
from src.common.template.renderer import get_renderer
from src.tenant.allergy_insight.formatter import AllergyInsightFormatter


@pytest.fixture
def client(tmp_path):
    """임시 DB + 테넌트 등록 후 FastAPI TestClient."""
    init_db(f"sqlite:///{tmp_path}/email.db")
    from src.main import register_tenants
    register_tenants()
    from src.web.app import app
    return TestClient(app)


def _seed_subscriber(token="tok-email", email="email@example.com"):
    sess = get_session_factory()()
    try:
        sub = SubscriberRepository.create(
            sess, "allergy-insight", email, "이메일", token,
            persona_code="clinician",
        )
        sess.commit()
        return sub.id
    finally:
        sess.close()


def _mirror_count():
    sess = get_session_factory()()
    try:
        from src.common.database.models import SubscriberTopicRequest
        return sess.query(SubscriberTopicRequest).count()
    finally:
        sess.close()


def test_et1_landing_valid_token_no_side_effect(client):
    """E-T1: 유효 토큰 GET 랜딩 → 폼 렌더 + 미러 행 미생성 (부작용 없음)."""
    _seed_subscriber(token="tok-et1")

    resp = client.get("/allergy-insight/persona/request?token=tok-et1")
    assert resp.status_code == 200
    assert "맞춤 콘텐츠 요청" in resp.text
    assert "새 주제 요청하기" in resp.text
    # GET 랜딩은 백엔드 호출·DB 쓰기를 하지 않는다 — 스캐너 자동 fetch 안전.
    assert _mirror_count() == 0


def test_et2_landing_section_context(client):
    """E-T2: section 파라미터 → 해당 분야 라벨 컨텍스트로 렌더."""
    _seed_subscriber(token="tok-et2")

    resp = client.get(
        "/allergy-insight/persona/request?token=tok-et2&section=key_papers"
    )
    assert resp.status_code == 200
    assert "주요 논문" in resp.text
    assert 'name="section" value="key_papers"' in resp.text

    # 미지의 section 값은 무시 (일반 랜딩으로 폴백)
    resp2 = client.get(
        "/allergy-insight/persona/request?token=tok-et2&section=bogus"
    )
    assert resp2.status_code == 200
    assert 'name="section"' not in resp2.text


def test_et3_landing_invalid_token(client):
    """E-T3: 무효 토큰 → 에러 페이지 (200, 안내 문구)."""
    resp = client.get("/allergy-insight/persona/request?token=nope")
    assert resp.status_code == 200
    assert "유효하지 않은" in resp.text


def test_et4_topic_request_section_passthrough(client, monkeypatch):
    """E-T4: topic-request POST 의 section → request_topic 페이로드 context.section."""
    _seed_subscriber(token="tok-et4")
    captured = {}

    async def fake_request_topic(payload):
        captured["payload"] = payload
        return {"coverage": "covered", "data": {"sections": []}}

    import src.web.routes_persona as rp
    monkeypatch.setattr(rp._persona_client, "request_topic", fake_request_topic)

    resp = client.post(
        "/allergy-insight/persona/topic-request",
        data={"token": "tok-et4", "request_type": "select",
              "section": "industry"},
    )
    assert resp.status_code == 200
    assert captured["payload"]["context"]["section"] == "industry"

    # 미지의 section 은 None 으로 정규화
    resp2 = client.post(
        "/allergy-insight/persona/topic-request",
        data={"token": "tok-et4", "request_type": "select",
              "section": "bogus"},
    )
    assert resp2.status_code == 200
    assert captured["payload"]["context"]["section"] is None


def test_et5_daily_report_persona_cta_guard():
    """E-T5: daily_report.html — persona_enabled 시 CTA·딥링크 노출, 아니면 숨김."""
    renderer = get_renderer()
    fmt = AllergyInsightFormatter()
    ctx = fmt._empty_context()
    ctx["top_headlines"] = [{"title": "H", "url": "http://x"}]
    ctx["company_digest"] = [{"company_name": "C", "count_7d": 1}]
    ctx["papers"] = [{"title": "P", "link": "http://p"}]

    ctx["persona_enabled"] = True
    on = renderer.render("allergy_insight/daily_report.html", ctx)
    assert "맞춤 콘텐츠 요청하기" in on
    assert on.count("__PERSONA_REQUEST_URL__") == 4  # 푸터 CTA + 섹션 3

    ctx["persona_enabled"] = False
    off = renderer.render("allergy_insight/daily_report.html", ctx)
    assert "맞춤 콘텐츠 요청하기" not in off
    assert "__PERSONA_REQUEST_URL__" not in off


def test_et6_formatter_persona_enabled_key():
    """E-T6: formatter format()/_empty_context() 가 persona_enabled 키를 포함."""
    fmt = AllergyInsightFormatter()
    assert "persona_enabled" in fmt._empty_context()
    ctx = fmt.format({"daily_report": {"report_date": "2026-05-21",
                                       "generated_at": "2026-05-21"}})
    assert "persona_enabled" in ctx
