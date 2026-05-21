"""페르소나 적응형 뉴스레터 N1 테스트.

N1-T1  마이그레이션 idempotency (구 스키마 ALTER 경로 + 재실행)
N1-T2  persona_code NULL → 'patient' 세그먼트 폴백
N1-T3  구독 폼 signup_meta 캐리 → Subscriber 페르소나 저장
N1-T3b signup_meta 없이 구독 → persona_code NULL (기존 동작 보존)
N1-T4  get_personas — api_key 미설정 시 [] 폴백 (구독 비차단)
N1-T5  update_persona 반영 + 빈 값 → NULL
N1-T6  SubscriberTopicRequest 미러 — request_id 멱등 조회
"""

import asyncio
import json

import pytest
from sqlalchemy import create_engine, text

from src.common.database.repository import (
    init_db,
    get_session_factory,
    SubscriberRepository,
    SubscriberTopicRequestRepository,
    _migrate_subscriber_persona_columns,
    _migrate_email_verification_signup_meta,
)
from src.common.subscription.manager import SubscriptionManager
from src.tenant.allergy_insight.persona_client import PersonaNewsletterClient


@pytest.fixture
def session(tmp_path):
    """임시 SQLite DB 세션 — init_db 로 페르소나 스키마가 적용된 상태."""
    init_db(f"sqlite:///{tmp_path}/n1.db")
    sess = get_session_factory()()
    yield sess
    sess.close()


def test_t1_migration_idempotent(tmp_path):
    """N1-T1: 구 스키마(페르소나 컬럼 없음)에 마이그레이션 2회 — ALTER + idempotent."""
    engine = create_engine(f"sqlite:///{tmp_path}/old.db")
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE subscribers ("
            "id INTEGER PRIMARY KEY, tenant_id VARCHAR(50), "
            "email VARCHAR(255), is_active BOOLEAN)"
        ))
        conn.execute(text(
            "CREATE TABLE email_verifications ("
            "id INTEGER PRIMARY KEY, tenant_id VARCHAR(50), email VARCHAR(255))"
        ))
        conn.commit()

    # 1차 — 실제 ALTER 경로 / 2차 — idempotent (예외 없이 통과해야 함)
    for _ in range(2):
        _migrate_subscriber_persona_columns(engine)
        _migrate_email_verification_signup_meta(engine)

    with engine.connect() as conn:
        sub_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(subscribers)"))}
        ev_cols = {r[1] for r in conn.execute(text("PRAGMA table_info(email_verifications)"))}

    assert {"persona_code", "purpose", "depth_level", "interests"} <= sub_cols
    assert "signup_meta" in ev_cols


def test_t2_null_persona_patient_fallback(session):
    """N1-T2: persona_code NULL 구독자는 'patient' 세그먼트에 합류."""
    SubscriberRepository.create(
        session, "allergy-insight", "null@example.com", "홍길동", "tok-t2"
    )
    session.flush()

    patient_seg = SubscriberRepository.get_active_by_persona(
        session, "allergy-insight", "patient"
    )
    assert any(s.email == "null@example.com" for s in patient_seg)

    clinician_seg = SubscriberRepository.get_active_by_persona(
        session, "allergy-insight", "clinician"
    )
    assert not any(s.email == "null@example.com" for s in clinician_seg)
    assert "patient" in SubscriberRepository.get_active_personas(
        session, "allergy-insight"
    )


def test_t3_signup_meta_carry(session):
    """N1-T3: request_subscribe signup_meta → verify_subscribe → 페르소나 4필드 저장."""
    mgr = SubscriptionManager()
    meta = {
        "persona_code": "clinician",
        "depth_level": "expert",
        "interests": ["peanut", "milk"],
    }
    ok, code, vid = mgr.request_subscribe(
        session, "allergy-insight", "t3@example.com", "홍길동", signup_meta=meta
    )
    assert ok
    session.flush()

    ok2, _, sub = mgr.verify_subscribe(session, vid, "t3@example.com", code)
    assert ok2
    assert sub.persona_code == "clinician"
    assert sub.depth_level == "expert"
    assert json.loads(sub.interests) == ["peanut", "milk"]


def test_t3b_no_persona_subscribe(session):
    """N1-T3b: signup_meta 없이 구독 → persona_code NULL (기존 동작 보존)."""
    mgr = SubscriptionManager()
    ok, code, vid = mgr.request_subscribe(
        session, "allergy-insight", "t3b@example.com", "홍길동"
    )
    assert ok
    session.flush()

    ok2, _, sub = mgr.verify_subscribe(session, vid, "t3b@example.com", code)
    assert ok2
    assert sub.persona_code is None
    assert sub.interests is None


def test_t4_get_personas_disabled():
    """N1-T4: api_key 미설정 → enabled False, get_personas() [] (구독 비차단)."""
    client = PersonaNewsletterClient(api_key="")
    assert client.enabled is False
    assert asyncio.run(client.get_personas()) == []


def test_t5_update_persona(session):
    """N1-T5: update_persona 로 페르소나·관심사 변경 + 빈 값 → NULL."""
    sub = SubscriberRepository.create(
        session, "allergy-insight", "t5@example.com", "홍길동", "tok-t5",
        persona_code="patient", interests=["egg"],
    )
    session.flush()

    SubscriberRepository.update_persona(
        session, sub.id, persona_code="researcher",
        depth_level="expert", interests=["soy", "wheat"],
    )
    assert sub.persona_code == "researcher"
    assert sub.depth_level == "expert"
    assert json.loads(sub.interests) == ["soy", "wheat"]

    # 빈 문자열/빈 리스트 → NULL 로 비움
    SubscriberRepository.update_persona(
        session, sub.id, persona_code="", interests=[]
    )
    assert sub.persona_code is None
    assert sub.interests is None


def test_t6_topic_request_mirror(session):
    """N1-T6: SubscriberTopicRequest 미러 — request_id 멱등 조회 + 결과 갱신."""
    sub = SubscriberRepository.create(
        session, "allergy-insight", "t6@example.com", "홍길동", "tok-t6"
    )
    session.flush()

    SubscriberTopicRequestRepository.create(
        session, tenant_id="allergy-insight", subscriber_id=sub.id,
        request_id="req-uuid-t6", request_type="select",
    )
    found = SubscriberTopicRequestRepository.get_by_request_id(session, "req-uuid-t6")
    assert found is not None
    assert found.coverage == "pending"

    SubscriberTopicRequestRepository.update_result(
        session, "req-uuid-t6", coverage="covered"
    )
    reloaded = SubscriberTopicRequestRepository.get_by_request_id(session, "req-uuid-t6")
    assert reloaded.coverage == "covered"
