"""페르소나 적응형 뉴스레터 N2 테스트.

N2-T1  format_topic_response — covered (sections 정규화)
N2-T2  format_topic_response — expandable (job_id 보관 + eta 환산)
N2-T3  format_topic_response — unsupported (alternatives, 빈 화면 아님)
N2-T4  build_topic_request — request_id 멱등 키 유일성 + 페이로드 계약
N2-T5  expansion-callback — status=ready → coverage covered 갱신
N2-T6  expansion-callback — 종료 행 중복 콜백 → no-op (dedup)
N2-T7  format_topic_response — data.editorial=null → 예외 없이 처리
N2-T8  expansion-callback — 미지 job_id → 멱등 무시
N2-T9  request_topic — api_key 미설정 → unsupported 폴백 (예외 미전파)
N2-T10 topic-request 엔드포인트 — select → 미러 적재 + covered 렌더
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.common.database.repository import (
    init_db,
    get_session_factory,
    SubscriberRepository,
    SubscriberTopicRequestRepository,
)
from src.tenant.allergy_insight.formatter import AllergyInsightFormatter
from src.tenant.allergy_insight.persona_client import (
    PersonaNewsletterClient,
    build_topic_request,
)

_FMT = AllergyInsightFormatter()


# --------------------------------------------------------------------------
# formatter coverage 3분기 (순수 함수)
# --------------------------------------------------------------------------

def test_t1_format_covered():
    """N2-T1: covered → sections 그대로 + coverage='covered'."""
    resp = {
        "coverage": "covered",
        "confidence": 0.9,
        "data": {
            "sections": [
                {"type": "key_papers", "title": "주요 논문",
                 "items": [{"title": "P1", "evidence_level": "A"}]},
            ],
            "editorial": {"text": "이번 주 핵심"},
        },
    }
    out = _FMT.format_topic_response(resp)
    assert out["coverage"] == "covered"
    assert len(out["sections"]) == 1
    assert out["sections"][0]["title"] == "주요 논문"
    assert out["editorial"]["text"] == "이번 주 핵심"


def test_t2_format_expandable():
    """N2-T2: expandable → job_id 보관 + eta_minutes 사용자 친화 환산."""
    resp = {
        "coverage": "expandable",
        "expansion": {"feasible": True, "source": "pubmed",
                      "eta_minutes": 90, "job_id": "job-xyz"},
    }
    out = _FMT.format_topic_response(resp)
    assert out["coverage"] == "expandable"
    assert out["job_id"] == "job-xyz"
    assert out["eta_text"] == "약 1시간 30분"


def test_t3_format_unsupported():
    """N2-T3: unsupported → message + alternatives. 빈 화면 금지."""
    resp = {
        "coverage": "unsupported",
        "fallback": {"reason": "out_of_domain", "message": "범위를 벗어납니다.",
                     "alternatives": [{"topic": "t", "label": "대체 주제"}]},
    }
    out = _FMT.format_topic_response(resp)
    assert out["coverage"] == "unsupported"
    assert out["message"] == "범위를 벗어납니다."
    assert out["alternatives"][0]["label"] == "대체 주제"

    # alternatives 가 비어도 안내 문구는 항상 존재 (빈 화면 금지)
    bare = _FMT.format_topic_response({"coverage": "unsupported"})
    assert bare["message"]
    assert bare["alternatives"] == []

    # 미지 coverage 값도 unsupported 로 방어
    assert _FMT.format_topic_response({})["coverage"] == "unsupported"


def test_t7_format_covered_editorial_null():
    """N2-T7: data.editorial=null 이어도 예외 없이 covered 처리."""
    out = _FMT.format_topic_response({
        "coverage": "covered",
        "data": {"sections": [], "editorial": None},
    })
    assert out["coverage"] == "covered"
    assert out["editorial"] is None
    assert out["sections"] == []


# --------------------------------------------------------------------------
# build_topic_request — 페이로드 빌더
# --------------------------------------------------------------------------

def test_t4_build_topic_request():
    """N2-T4: request_id 는 호출마다 유일 + 정본 계약 페이로드 형태."""
    sub = SimpleNamespace(
        persona_code="clinician", depth_level="expert",
        interests='["peanut", "milk"]',
    )
    p1 = build_topic_request(subscriber=sub, request_type="transform",
                             topic="땅콩 OIT")
    p2 = build_topic_request(subscriber=sub, request_type="transform",
                             topic="땅콩 OIT")

    assert p1["request_id"] != p2["request_id"]  # 멱등 키 — 매 호출 유일
    assert p1["request_type"] == "transform"
    assert p1["topic"] == "땅콩 OIT"
    assert p1["subscriber_ref"]["persona_code"] == "clinician"
    assert p1["subscriber_ref"]["depth"] == "expert"
    assert p1["subscriber_ref"]["interests"] == ["peanut", "milk"]

    # persona_code 미설정 구독자 → patient 폴백
    bare = SimpleNamespace(persona_code=None, depth_level=None, interests=None)
    pb = build_topic_request(subscriber=bare, request_type="select")
    assert pb["subscriber_ref"]["persona_code"] == "patient"
    assert pb["subscriber_ref"]["interests"] == []


def test_t9_request_topic_disabled():
    """N2-T9: api_key 미설정 → request_topic 은 unsupported 폴백 (예외 미전파)."""
    client = PersonaNewsletterClient(api_key="")
    assert client.enabled is False
    sub = SimpleNamespace(persona_code="patient", depth_level="practical",
                          interests=None)
    payload = build_topic_request(subscriber=sub, request_type="select")
    out = asyncio.run(client.request_topic(payload))
    assert out["coverage"] == "unsupported"
    assert asyncio.run(client.get_job("any")) == {"status": "pending"}


# --------------------------------------------------------------------------
# expansion-callback 엔드포인트 (TestClient)
# --------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    """임시 DB + 테넌트 등록 후 FastAPI TestClient."""
    init_db(f"sqlite:///{tmp_path}/n2.db")
    from src.main import register_tenants
    register_tenants()
    from src.web.app import app
    return TestClient(app)


def _seed_request(*, job_id, coverage, request_id="req-seed",
                  token="tok-seed", email="cb@example.com"):
    """구독자 + topic-request 미러 행 1건 시드."""
    sess = get_session_factory()()
    try:
        sub = SubscriberRepository.create(
            sess, "allergy-insight", email, "콜백", token
        )
        sess.flush()
        SubscriberTopicRequestRepository.create(
            sess, tenant_id="allergy-insight", subscriber_id=sub.id,
            request_id=request_id, request_type="transform", topic="X",
        )
        SubscriberTopicRequestRepository.update_result(
            sess, request_id, coverage=coverage, job_id=job_id,
        )
        sess.commit()
        return sub.id
    finally:
        sess.close()


def _coverage_of(job_id):
    sess = get_session_factory()()
    try:
        row = SubscriberTopicRequestRepository.get_by_job_id(sess, job_id)
        return row.coverage if row else None
    finally:
        sess.close()


def test_t5_callback_ready_updates_coverage(client):
    """N2-T5: status=ready 콜백 → 미러 행 coverage covered 로 갱신."""
    _seed_request(job_id="job-t5", coverage="expandable")

    resp = client.post(
        "/api/newsletter/expansion-callback",
        json={"job_id": "job-t5", "status": "ready",
              "data": {"sections": []}},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert _coverage_of("job-t5") == "covered"


def test_t6_callback_duplicate_noop(client):
    """N2-T6: 이미 종료된 행에 중복 콜백 → no-op (dedup), coverage 불변."""
    _seed_request(job_id="job-t6", coverage="covered")  # 이미 종료 상태

    resp = client.post(
        "/api/newsletter/expansion-callback",
        json={"job_id": "job-t6", "status": "failed"},  # 뒤집기 시도
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "duplicate"
    assert _coverage_of("job-t6") == "covered"  # 변경 안 됨


def test_t8_callback_unknown_job_ignored(client):
    """N2-T8: 미러 행 없는 job_id 콜백 → 멱등 무시 (200)."""
    resp = client.post(
        "/api/newsletter/expansion-callback",
        json={"job_id": "job-does-not-exist", "status": "ready"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_t8b_callback_failed_to_unsupported(client):
    """N2-T8b: status=failed 콜백 → coverage unsupported."""
    _seed_request(job_id="job-fail", coverage="expandable")
    resp = client.post(
        "/api/newsletter/expansion-callback",
        json={"job_id": "job-fail", "status": "failed", "error": "no source"},
    )
    assert resp.status_code == 200
    assert _coverage_of("job-fail") == "unsupported"


# --------------------------------------------------------------------------
# topic-request 엔드포인트 (request_topic 모킹)
# --------------------------------------------------------------------------

def test_t10_topic_request_endpoint_select(client, monkeypatch):
    """N2-T10: select 요청 → 미러 행 적재 + covered 결과 렌더."""
    _seed_request(job_id="job-x", coverage="covered",
                  request_id="req-x", token="tok-sel", email="sel@example.com")

    async def fake_request_topic(payload):
        return {"coverage": "covered",
                "data": {"sections": [
                    {"type": "key_papers", "title": "맞춤 논문 섹션",
                     "items": []}],
                    "editorial": None}}

    import src.web.routes_persona as rp
    monkeypatch.setattr(rp._persona_client, "request_topic",
                        fake_request_topic)

    resp = client.post(
        "/allergy-insight/persona/topic-request",
        data={"token": "tok-sel", "request_type": "select"},
    )
    assert resp.status_code == 200
    assert "맞춤 논문 섹션" in resp.text

    # 미러 행이 새로 적재되었는지 (req-x 시드 + 신규 1건)
    sess = get_session_factory()()
    try:
        sub = SubscriberRepository.get_by_unsubscribe_token(sess, "tok-sel")
        rows = SubscriberTopicRequestRepository.list_by_subscriber(
            sess, "allergy-insight", sub.id
        )
        assert any(r.request_type == "select" and r.coverage == "covered"
                   for r in rows)
    finally:
        sess.close()


def test_t10b_topic_request_transform_requires_topic(client):
    """N2-T10b: transform 인데 주제 미입력 → 안내 메시지, 백엔드 호출 없음."""
    _seed_request(job_id="job-y", coverage="covered",
                  request_id="req-y", token="tok-tr", email="tr@example.com")

    resp = client.post(
        "/allergy-insight/persona/topic-request",
        data={"token": "tok-tr", "request_type": "transform", "topic": ""},
    )
    assert resp.status_code == 200
    assert "주제를 입력" in resp.text
