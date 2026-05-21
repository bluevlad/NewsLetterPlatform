"""페르소나 적응형 뉴스레터 (N2) — 콘텐츠 선택·변형 라우터.

엔드포인트:
  POST /api/newsletter/expansion-callback   — AllergyInsight expandable job 완료 콜백
  POST /{tenant_id}/persona/topic-request   — 수신자 콘텐츠 선택(select)·변형(transform)
  GET  /{tenant_id}/persona/job/{job_id}    — 비동기 job 결과 폴링 (콜백 누락 폴백)

설계: PERSONA_ADAPTIVE_NEWSLETTER_N1_N2_DESIGN.md §9
정본 API 계약: AllergyInsight persona-adaptive-newsletter-plan.md §3.2
"""

import json
import logging

from fastapi import APIRouter, Request, Form, Header
from fastapi.responses import HTMLResponse, JSONResponse

from ..config import settings
from ..common.database.repository import (
    get_session_factory,
    SubscriberRepository,
    SubscriberTopicRequestRepository,
)
from ..tenant.allergy_insight.persona_client import (
    PersonaNewsletterClient,
    build_topic_request,
)
from ..tenant.allergy_insight.formatter import AllergyInsightFormatter
from .shared import templates, get_tenant_or_404

logger = logging.getLogger(__name__)

router = APIRouter()

_persona_client = PersonaNewsletterClient()
_formatter = AllergyInsightFormatter()

# coverage 종료 상태 — 콜백/폴링 중복 처리 dedup 기준.
_TERMINAL_COVERAGE = {"covered", "unsupported"}

_RESULT_TEMPLATE = "persona_result.html"


def _callback_url() -> str:
    """expandable job 완료 시 AllergyInsight 가 역호출할 콜백 URL."""
    return settings.web_base_url.rstrip("/") + "/api/newsletter/expansion-callback"


def _error_page(request: Request, tenant, token: str, message: str):
    return templates.TemplateResponse(_RESULT_TEMPLATE, {
        "request": request,
        "tenant": tenant,
        "token": token,
        "error": message,
    })


@router.post("/api/newsletter/expansion-callback")
async def expansion_callback(
    request: Request,
    x_newsletter_key: str = Header(default=None),
):
    """AllergyInsight expandable job 완료 역호출 (정본 계약 §3.2.4).

    인증: `X-Newsletter-Key` 헤더가 오면 검증한다. 정본 webhook 발신부가 커스텀
    헤더를 싣지 않을 수 있어(설계 Q3), 헤더 부재는 거부하지 않고 job_id 매칭으로
    방어한다 — 매칭 미러 행이 없으면 200 + 무시(멱등, 정보 누출 방지).

    콜백 실패가 백엔드 job 을 막지 않도록 어떤 경우에도 빠르게 200 을 반환한다.
    """
    expected = settings.allergy_insight_newsletter_api_key
    if x_newsletter_key and expected and x_newsletter_key != expected:
        logger.warning("expansion-callback: X-Newsletter-Key 불일치 — 거부")
        return JSONResponse({"status": "rejected"}, status_code=403)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "ignored", "reason": "bad_json"})

    job_id = payload.get("job_id")
    request_id = payload.get("request_id")
    status = payload.get("status")
    if not job_id and not request_id:
        return JSONResponse({"status": "ignored", "reason": "no_key"})

    db = get_session_factory()()
    try:
        row = None
        if job_id:
            row = SubscriberTopicRequestRepository.get_by_job_id(db, job_id)
        if not row and request_id:
            row = SubscriberTopicRequestRepository.get_by_request_id(
                db, request_id
            )
        if not row:
            # 미러 행 없음 — 알 수 없는 job. 멱등 무시.
            return JSONResponse({"status": "ignored", "reason": "unknown_job"})

        # 이미 종료된 행이면 중복 콜백 → no-op (dedup).
        if row.coverage in _TERMINAL_COVERAGE:
            return JSONResponse({"status": "duplicate"})

        if status == "ready":
            new_coverage = "covered"
        elif status == "failed":
            new_coverage = "unsupported"
        else:
            # pending/collecting 등 — 아직 종료 아님. 무시.
            return JSONResponse({"status": "noop", "reason": status})

        SubscriberTopicRequestRepository.update_result(
            db, row.request_id, coverage=new_coverage,
            result_json=json.dumps(payload, ensure_ascii=False),
        )
        db.commit()
        logger.info(
            f"expansion-callback 반영: job={job_id} → {new_coverage}"
        )
        return JSONResponse({"status": "ok"})
    except Exception as e:
        db.rollback()
        logger.error(f"expansion-callback 처리 오류: {e}")
        # 콜백 실패가 job 을 막지 않도록 200 반환.
        return JSONResponse({"status": "error"})
    finally:
        db.close()


@router.post("/{tenant_id}/persona/topic-request", response_class=HTMLResponse)
async def persona_topic_request(
    request: Request,
    tenant_id: str,
    token: str = Form(...),
    request_type: str = Form(default="transform"),
    topic: str = Form(default=""),
    depth: str = Form(default=""),
    framing: str = Form(default=""),
):
    """수신자 콘텐츠 선택·변형 요청 (셀프 서비스).

    select   — 내 페르소나 맞춤 콘텐츠 전체 받기 (topic 불필요).
    transform — 자유 입력 주제 요청 (topic 필수). 미보유 시 expandable.
    """
    tenant = get_tenant_or_404(tenant_id)
    topic = (topic or "").strip()
    if request_type not in ("select", "transform"):
        request_type = "transform"

    db = get_session_factory()()
    try:
        subscriber = SubscriberRepository.get_by_unsubscribe_token(db, token)
        if not subscriber or subscriber.tenant_id != tenant_id:
            return _error_page(
                request, tenant, token,
                "유효하지 않은 링크이거나 구독이 해지된 상태입니다.",
            )

        if request_type == "transform" and not topic:
            return _error_page(
                request, tenant, token, "요청할 주제를 입력해주세요.",
            )

        intent = {
            "depth": depth or subscriber.depth_level or "practical",
            "language": "ko",
            "framing": framing or None,
        }
        context = {
            "callback_url": _callback_url(),
            "section": None,
            "current_content_ids": [],
        }
        payload = build_topic_request(
            subscriber=subscriber,
            request_type=request_type,
            topic=topic or None,
            intent=intent,
            context=context,
            tenant_id=tenant_id,
        )
        request_id = payload["request_id"]

        # 미러 행을 먼저 적재 — expandable 콜백이 응답보다 빨라도 매칭되도록.
        SubscriberTopicRequestRepository.create(
            db, tenant_id=tenant_id, subscriber_id=subscriber.id,
            request_id=request_id, request_type=request_type,
            topic=topic or None,
        )
        db.commit()

        resp = await _persona_client.request_topic(payload)
        result = _formatter.format_topic_response(resp)

        SubscriberTopicRequestRepository.update_result(
            db, request_id, coverage=result["coverage"],
            job_id=result.get("job_id"),
            result_json=json.dumps(resp, ensure_ascii=False),
        )
        db.commit()

        return templates.TemplateResponse(_RESULT_TEMPLATE, {
            "request": request,
            "tenant": tenant,
            "token": token,
            "result": result,
            "topic": topic,
            "request_type": request_type,
        })
    except Exception as e:
        db.rollback()
        logger.error(f"topic-request 처리 오류: {e}")
        return _error_page(
            request, tenant, token,
            "요청 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
        )
    finally:
        db.close()


@router.get("/{tenant_id}/persona/job/{job_id}", response_class=HTMLResponse)
async def persona_job_poll(
    request: Request, tenant_id: str, job_id: str, token: str = "",
):
    """비동기 수집 job 결과 조회 (콜백 누락 대비 수동 폴링)."""
    tenant = get_tenant_or_404(tenant_id)

    db = get_session_factory()()
    try:
        row = SubscriberTopicRequestRepository.get_by_job_id(db, job_id)
        if not row or row.tenant_id != tenant_id:
            return _error_page(
                request, tenant, token, "요청 내역을 찾을 수 없습니다.",
            )

        # 콜백으로 이미 종료된 경우 — 저장된 결과를 그대로 렌더.
        if row.coverage in _TERMINAL_COVERAGE and row.result_json:
            stored = json.loads(row.result_json)
            result = _formatter.format_topic_response(
                stored if "coverage" in stored
                else {"coverage": "covered",
                      "data": stored.get("data") or {}}
            )
            return templates.TemplateResponse(_RESULT_TEMPLATE, {
                "request": request, "tenant": tenant, "token": token,
                "result": result, "topic": row.topic,
            })

        # 아직 진행 중 — 백엔드 폴링.
        job = await _persona_client.get_job(job_id)
        status = job.get("status")
        if status == "ready":
            result = _formatter.format_topic_response(
                {"coverage": "covered", "data": job.get("data") or {}}
            )
            SubscriberTopicRequestRepository.update_result(
                db, row.request_id, coverage="covered",
                result_json=json.dumps(job, ensure_ascii=False),
            )
            db.commit()
        elif status == "failed":
            result = _formatter.format_topic_response(
                {"coverage": "unsupported",
                 "fallback": {"message": "주제 수집에 실패했습니다."}}
            )
            SubscriberTopicRequestRepository.update_result(
                db, row.request_id, coverage="unsupported",
                result_json=json.dumps(job, ensure_ascii=False),
            )
            db.commit()
        else:
            # pending/collecting — expandable 안내 유지 (계속 폴링 가능).
            result = {
                "coverage": "expandable",
                "job_id": job_id,
                "eta_text": _formatter._humanize_eta(
                    job.get("eta_minutes", 30)
                ),
                "polling": True,
            }
        return templates.TemplateResponse(_RESULT_TEMPLATE, {
            "request": request, "tenant": tenant, "token": token,
            "result": result, "topic": row.topic,
        })
    finally:
        db.close()
