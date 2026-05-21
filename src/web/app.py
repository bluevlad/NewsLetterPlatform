"""
NewsLetterPlatform 웹 애플리케이션
멀티테넌트 이메일 인증 기반 구독 시스템
"""

import json
import logging
import threading
from pathlib import Path as _Path
from urllib.parse import urlparse

from pathlib import Path as _Path

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import text
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from ..config import settings
from ..common.database.repository import get_session_factory
from ..common.subscription.manager import SubscriptionManager
from ..common.subscription.email_service import send_verification_email
from ..common.scheduler.jobs import send_welcome_newsletter
from ..common.database.repository import (
    get_session, SendHistoryRepository, NewsletterArchiveRepository,
    SubscriberRepository,
)
from ..common.security import (
    is_honeypot_filled,
    verify_turnstile,
    get_client_ip,
)
from ..tenant.registry import get_registry
from ..tenant.allergy_insight.persona_client import (
    PersonaNewsletterClient, INTEREST_ALLERGENS, persona_default_depth,
)
from .shared import templates, templates_dir, get_db, get_tenant_or_404
from .admin import admin_router

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# 리버스 프록시 base path prefix
_base = settings.root_path

logger = logging.getLogger(__name__)

# FastAPI 앱 생성
app = FastAPI(
    title="NewsLetterPlatform",
    description="멀티테넌트 뉴스레터 통합 플랫폼",
    version="1.0.0",
    root_path=settings.root_path,
)

# Rate limiter — 리버스 프록시 X-Forwarded-For 고려한 키 함수 사용
def _rate_limit_key(request: Request) -> str:
    return get_client_ip(request)

limiter = Limiter(key_func=_rate_limit_key)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """rate limit 초과 — 봇에 정보 노출 최소화 (구체 메시지 + 429)"""
    logger.warning("rate limit 초과: ip=%s, path=%s", get_client_ip(request), request.url.path)
    return JSONResponse(
        status_code=429,
        content={"error": "요청이 너무 많습니다. 잠시 후 다시 시도해주세요."},
    )

class CSRFOriginCheckMiddleware(BaseHTTPMiddleware):
    """CSRF 방지: POST 요청의 Origin/Referer가 허용된 호스트인지 검증.

    BaseHTTPMiddleware 안에서 HTTPException 을 raise 하면 Starlette 가
    잡지 못해 500 으로 떨어지므로 JSONResponse 를 직접 반환한다.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method == "POST":
            origin = request.headers.get("origin") or request.headers.get("referer")
            if origin:
                parsed = urlparse(origin)
                allowed_hosts = {
                    urlparse(settings.web_base_url).hostname,
                    "localhost",
                    "127.0.0.1",
                }
                if settings.csrf_allowed_hosts:
                    allowed_hosts.update(
                        h.strip() for h in settings.csrf_allowed_hosts.split(",") if h.strip()
                    )
                if parsed.hostname not in allowed_hosts:
                    logger.warning(
                        "CSRF check failed: origin=%s allowed=%s", origin, sorted(allowed_hosts)
                    )
                    return JSONResponse(
                        status_code=403,
                        content={"error": "Forbidden: invalid origin"},
                    )
        return await call_next(request)

# CSRF 미들웨어 적용
app.add_middleware(CSRFOriginCheckMiddleware)

# Starlette SessionMiddleware
# secret_key: 앱 시작 시마다 새 키 생성 (in-memory 세션과 동일 lifecycle)
import secrets as _secrets
app.add_middleware(SessionMiddleware, secret_key=_secrets.token_urlsafe(32))

# 구독 매니저
subscription_manager = SubscriptionManager()

# 페르소나 적응형 뉴스레터 클라이언트 (api_key 미설정 시 enabled=False — 폼에서 자동 숨김)
persona_client = PersonaNewsletterClient()

# 정적 파일 서빙
_static_dir = _Path(__file__).parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
    # 서비스 소개 페이지 자산 — /css/service-landing.css 접근용
    if (_static_dir / "css").is_dir():
        app.mount("/css", StaticFiles(directory=str(_static_dir / "css")), name="intro-css")


@app.get("/intro.html", include_in_schema=False)
async def intro_page():
    """서비스 소개 페이지 (게이트웨이에서 진입 시)"""
    from fastapi.responses import FileResponse
    return FileResponse(_static_dir / "intro.html")

# Admin 라우터 등록 (/{tenant_id} 보다 먼저)
app.include_router(admin_router)


def resolve_template(tenant_id: str, template_name: str) -> str:
    """테넌트별 템플릿 오버라이드 지원

    overrides/{tenant_id}/{template_name} 파일이 존재하면 우선 사용,
    없으면 기본 템플릿 반환.
    """
    override_path = templates_dir / "overrides" / tenant_id / template_name
    if override_path.exists():
        return f"overrides/{tenant_id}/{template_name}"
    return template_name


# ==================== Health Check ====================

@app.get("/api/health", response_class=JSONResponse)
async def api_health():
    """Liveness 엔드포인트 — 앱 프로세스 응답 + DB 연결 확인"""
    try:
        with get_session() as session:
            session.execute(text("SELECT 1"))
        return JSONResponse(content={"status": "ok"}, status_code=200)
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            content={"status": "unhealthy", "error": str(e)},
            status_code=503,
        )


@app.get("/api/health/scheduler", response_class=JSONResponse)
async def api_health_scheduler():
    """Readiness 엔드포인트 — 당일 발송 통계 기반 스케줄러 상태"""
    try:
        registry = get_registry()
        total = 0
        failed = 0
        tenant_stats = {}

        with get_session() as session:
            for tid in registry.get_active_ids():
                stats = SendHistoryRepository.get_today_stats(session, tid)
                tenant_stats[tid] = stats
                total += stats["total"]
                failed += stats["failed"]

        return JSONResponse(content={
            "total": total,
            "failed": failed,
            "tenants": tenant_stats,
        })
    except Exception as e:
        logger.error(f"Scheduler health check failed: {e}")
        return JSONResponse(
            content={"total": 0, "failed": 0, "error": str(e)},
            status_code=503,
        )


# ==================== 랜딩 페이지 ====================

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """테넌트 목록 랜딩 페이지"""
    registry = get_registry()
    tenants = registry.get_all()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "tenants": tenants,
    })


# ==================== 구독 플로우 ====================

@app.get("/{tenant_id}/subscribe", response_class=HTMLResponse)
async def subscribe_form(request: Request, tenant_id: str):
    """구독 신청 폼"""
    tenant = get_tenant_or_404(tenant_id)
    personas = await persona_client.get_personas()
    return templates.TemplateResponse(resolve_template(tenant_id, "subscribe.html"), {
        "request": request,
        "tenant": tenant,
        "personas": personas,
        "interest_allergens": INTEREST_ALLERGENS if personas else [],
        "persona_code": "",
        "selected_interests": [],
    })


@app.post("/{tenant_id}/subscribe", response_class=HTMLResponse)
@limiter.limit(settings.subscribe_rate_limit_ip)
async def subscribe_submit(
    request: Request,
    tenant_id: str,
    email: str = Form(...),
    name: str = Form(default=""),
    website: str = Form(default=""),  # honeypot — 사람은 비워둠
    cf_turnstile_response: str = Form(default="", alias="cf-turnstile-response"),
    persona_code: str = Form(default=""),
    interests: list[str] = Form(default=[]),
):
    """구독 신청 처리 - 인증코드 발송"""
    tenant = get_tenant_or_404(tenant_id)
    personas = await persona_client.get_personas()

    def _render_form(error_msg: str):
        return templates.TemplateResponse(resolve_template(tenant_id, "subscribe.html"), {
            "request": request,
            "tenant": tenant,
            "error": error_msg,
            "email": email,
            "name": name,
            "personas": personas,
            "interest_allergens": INTEREST_ALLERGENS if personas else [],
            "persona_code": persona_code,
            "selected_interests": interests,
        })

    # Honeypot — 봇이 모든 필드를 채우면 silent drop (200 + 일반 에러로 위장, 학습 회피)
    if is_honeypot_filled(website):
        logger.warning("honeypot trigger: ip=%s, email=%s", get_client_ip(request), email)
        return _render_form("오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

    # Cloudflare Turnstile (env 미설정 시 자동 비활성화)
    if settings.turnstile_secret_key:
        ok = await verify_turnstile(
            cf_turnstile_response,
            settings.turnstile_secret_key,
            remote_ip=get_client_ip(request),
        )
        if not ok:
            logger.warning("Turnstile 검증 실패: ip=%s, email=%s",
                          get_client_ip(request), email)
            return _render_form("보안 검증에 실패했습니다. 페이지를 새로고침 후 다시 시도해주세요.")

    # 페르소나 선택 검증 — 카탈로그에 있는 code 만 수용, interests 는 화이트리스트 필터.
    # 미선택(빈 값) 시 signup_meta=None → patient 폴백으로 동작.
    signup_meta = None
    if personas and persona_code:
        valid_persona_codes = {p.get("code") for p in personas}
        if persona_code in valid_persona_codes:
            valid_interest_codes = {a["code"] for a in INTEREST_ALLERGENS}
            clean_interests = [i for i in interests if i in valid_interest_codes]
            signup_meta = {
                "persona_code": persona_code,
                "depth_level": persona_default_depth(personas, persona_code),
                "interests": clean_interests,
            }

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        success, code_or_msg, verification_id = subscription_manager.request_subscribe(
            db, tenant_id, email, name, signup_meta=signup_meta
        )

        if not success:
            return _render_form(code_or_msg)

        db.commit()

        # 인증 이메일 발송
        email_sent = send_verification_email(
            tenant_name=tenant.display_name,
            tenant_subject_prefix=tenant.email_subject_prefix,
            email=email.strip().lower(),
            name=name.strip(),
            code=code_or_msg,
        )

        if email_sent:
            return RedirectResponse(
                url=f"{_base}/{tenant_id}/verify/{verification_id}?email={email.strip().lower()}",
                status_code=303
            )
        else:
            return _render_form("이메일 발송에 실패했습니다. 잠시 후 다시 시도해주세요.")

    except Exception as e:
        db.rollback()
        logger.error(f"구독 신청 처리 오류: {e}")
        return _render_form("오류가 발생했습니다. 잠시 후 다시 시도해주세요.")
    finally:
        db.close()


@app.get("/{tenant_id}/verify/{verification_id}", response_class=HTMLResponse)
async def verify_form(request: Request, tenant_id: str, verification_id: int, email: str = ""):
    """인증코드 입력 폼"""
    tenant = get_tenant_or_404(tenant_id)
    return templates.TemplateResponse(resolve_template(tenant_id, "verify_code.html"), {
        "request": request,
        "tenant": tenant,
        "verification_id": verification_id,
        "email": email,
    })


@app.post("/{tenant_id}/verify", response_class=HTMLResponse)
async def verify_submit(
    request: Request,
    tenant_id: str,
    verification_id: int = Form(...),
    email: str = Form(...),
    code: str = Form(...)
):
    """인증코드 확인"""
    tenant = get_tenant_or_404(tenant_id)

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        success, message, subscriber = subscription_manager.verify_subscribe(
            db, verification_id, email, code
        )

        if not success:
            return templates.TemplateResponse(resolve_template(tenant_id, "verify_code.html"), {
                "request": request,
                "tenant": tenant,
                "verification_id": verification_id,
                "email": email,
                "error": message,
            })

        db.commit()

        # 웰컴 뉴스레터 비동기 발송 (응답 지연 방지)
        threading.Thread(
            target=send_welcome_newsletter,
            args=(tenant_id, email.strip().lower()),
            daemon=True
        ).start()

        return RedirectResponse(
            url=f"{_base}/{tenant_id}/result?email={email}",
            status_code=303
        )

    except Exception as e:
        db.rollback()
        logger.error(f"인증 처리 오류: {e}")
        return templates.TemplateResponse(resolve_template(tenant_id, "verify_code.html"), {
            "request": request,
            "tenant": tenant,
            "verification_id": verification_id,
            "email": email,
            "error": "오류가 발생했습니다.",
        })
    finally:
        db.close()


@app.get("/{tenant_id}/result", response_class=HTMLResponse)
async def result_page(request: Request, tenant_id: str, email: str = ""):
    """구독 완료 페이지"""
    tenant = get_tenant_or_404(tenant_id)
    return templates.TemplateResponse(resolve_template(tenant_id, "result.html"), {
        "request": request,
        "tenant": tenant,
        "email": email,
    })


# ==================== 아카이브 ====================

@app.get("/archive", response_class=HTMLResponse)
async def archive_all(request: Request):
    """전체 테넌트 뉴스레터 아카이브 통합 목록"""
    registry = get_registry()
    tenants = registry.get_all()
    tenant_map = {t.tenant_id: t for t in tenants}

    with get_session() as session:
        archives = NewsletterArchiveRepository.get_all_list(session, limit=100)
        # 테넌트 → 월별 그룹핑
        grouped_by_tenant = {}
        for archive in archives:
            tid = archive.tenant_id
            if tid not in grouped_by_tenant:
                grouped_by_tenant[tid] = {}
            month_key = archive.sent_date.strftime("%Y년 %m월")
            if month_key not in grouped_by_tenant[tid]:
                grouped_by_tenant[tid][month_key] = []
            type_labels = {"daily": "일일", "weekly": "주간", "monthly": "월간"}
            grouped_by_tenant[tid][month_key].append({
                "id": archive.id,
                "tenant_id": tid,
                "date": archive.sent_date.strftime("%m/%d"),
                "weekday": ["월", "화", "수", "목", "금", "토", "일"][archive.sent_date.weekday()],
                "type": archive.newsletter_type,
                "type_label": type_labels.get(archive.newsletter_type, archive.newsletter_type),
                "subject": archive.subject or "",
            })

    return templates.TemplateResponse("archive_all.html", {
        "request": request,
        "grouped_by_tenant": grouped_by_tenant,
        "tenant_map": tenant_map,
    })


@app.get("/{tenant_id}/archive", response_class=HTMLResponse)
async def archive_list(request: Request, tenant_id: str):
    """뉴스레터 아카이브 목록"""
    tenant = get_tenant_or_404(tenant_id)

    with get_session() as session:
        archives = NewsletterArchiveRepository.get_list(session, tenant_id, limit=50)
        # 월별 그룹핑
        grouped = {}
        for archive in archives:
            month_key = archive.sent_date.strftime("%Y년 %m월")
            if month_key not in grouped:
                grouped[month_key] = []
            type_labels = {"daily": "일일", "weekly": "주간", "monthly": "월간"}
            grouped[month_key].append({
                "id": archive.id,
                "date": archive.sent_date.strftime("%m/%d"),
                "weekday": ["월", "화", "수", "목", "금", "토", "일"][archive.sent_date.weekday()],
                "type": archive.newsletter_type,
                "type_label": type_labels.get(archive.newsletter_type, archive.newsletter_type),
                "subject": archive.subject or "",
            })

    return templates.TemplateResponse("archive_list.html", {
        "request": request,
        "tenant": tenant,
        "grouped": grouped,
    })


@app.get("/{tenant_id}/archive/{archive_id}", response_class=HTMLResponse)
async def archive_detail(request: Request, tenant_id: str, archive_id: int):
    """뉴스레터 아카이브 상세 (HTML 원본 렌더링)"""
    tenant = get_tenant_or_404(tenant_id)

    with get_session() as session:
        archive = NewsletterArchiveRepository.get_by_id(session, archive_id)
        if not archive or archive.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="아카이브를 찾을 수 없습니다")

        # 구독해지 링크를 아카이브 안내로 대체
        html = archive.html_content.replace(
            "__UNSUBSCRIBE_URL__",
            f"{_base}/{tenant_id}/archive"
        )

        return HTMLResponse(content=html)


# ==================== 구독 해지 플로우 ====================

@app.get("/{tenant_id}/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_form(request: Request, tenant_id: str):
    """구독 해지 신청 폼"""
    tenant = get_tenant_or_404(tenant_id)
    return templates.TemplateResponse(resolve_template(tenant_id, "unsubscribe_request.html"), {
        "request": request,
        "tenant": tenant,
    })


@app.post("/{tenant_id}/unsubscribe", response_class=HTMLResponse)
@limiter.limit(settings.subscribe_rate_limit_ip)
async def unsubscribe_submit(
    request: Request,
    tenant_id: str,
    email: str = Form(...),
    website: str = Form(default=""),  # honeypot
    cf_turnstile_response: str = Form(default="", alias="cf-turnstile-response"),
):
    """구독 해지 신청 처리 - 인증코드 발송"""
    tenant = get_tenant_or_404(tenant_id)

    if is_honeypot_filled(website):
        logger.warning("honeypot trigger (unsubscribe): ip=%s, email=%s",
                      get_client_ip(request), email)
        return templates.TemplateResponse(resolve_template(tenant_id, "unsubscribe_request.html"), {
            "request": request,
            "tenant": tenant,
            "error": "오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
            "email": email,
        })

    if settings.turnstile_secret_key:
        ok = await verify_turnstile(
            cf_turnstile_response,
            settings.turnstile_secret_key,
            remote_ip=get_client_ip(request),
        )
        if not ok:
            logger.warning("Turnstile 검증 실패 (unsubscribe): ip=%s, email=%s",
                          get_client_ip(request), email)
            return templates.TemplateResponse(resolve_template(tenant_id, "unsubscribe_request.html"), {
                "request": request,
                "tenant": tenant,
                "error": "보안 검증에 실패했습니다. 페이지를 새로고침 후 다시 시도해주세요.",
                "email": email,
            })

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        success, code_or_msg, verification_id = subscription_manager.request_unsubscribe(
            db, tenant_id, email
        )

        if not success:
            return templates.TemplateResponse(resolve_template(tenant_id, "unsubscribe_request.html"), {
                "request": request,
                "tenant": tenant,
                "error": code_or_msg,
                "email": email,
            })

        db.commit()

        email_sent = send_verification_email(
            tenant_name=tenant.display_name,
            tenant_subject_prefix=tenant.email_subject_prefix,
            email=email.strip().lower(),
            name="",
            code=code_or_msg,
            verification_type="unsubscribe"
        )

        if email_sent:
            return RedirectResponse(
                url=f"{_base}/{tenant_id}/unsubscribe/verify/{verification_id}?email={email.strip().lower()}",
                status_code=303
            )
        else:
            return templates.TemplateResponse(resolve_template(tenant_id, "unsubscribe_request.html"), {
                "request": request,
                "tenant": tenant,
                "error": "이메일 발송에 실패했습니다. 잠시 후 다시 시도해주세요.",
                "email": email,
            })

    except Exception as e:
        db.rollback()
        logger.error(f"구독 해지 신청 오류: {e}")
        return templates.TemplateResponse(resolve_template(tenant_id, "unsubscribe_request.html"), {
            "request": request,
            "tenant": tenant,
            "error": "오류가 발생했습니다.",
            "email": email,
        })
    finally:
        db.close()


@app.get("/{tenant_id}/unsubscribe/verify/{verification_id}", response_class=HTMLResponse)
async def unsubscribe_verify_form(
    request: Request, tenant_id: str, verification_id: int, email: str = ""
):
    """구독 해지 인증코드 입력 폼"""
    tenant = get_tenant_or_404(tenant_id)
    return templates.TemplateResponse(resolve_template(tenant_id, "unsubscribe_verify.html"), {
        "request": request,
        "tenant": tenant,
        "verification_id": verification_id,
        "email": email,
    })


@app.post("/{tenant_id}/unsubscribe/verify", response_class=HTMLResponse)
async def unsubscribe_verify_submit(
    request: Request,
    tenant_id: str,
    verification_id: int = Form(...),
    email: str = Form(...),
    code: str = Form(...)
):
    """구독 해지 인증코드 확인"""
    tenant = get_tenant_or_404(tenant_id)

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        success, message = subscription_manager.verify_unsubscribe(
            db, verification_id, email, code
        )

        if not success:
            return templates.TemplateResponse(resolve_template(tenant_id, "unsubscribe_verify.html"), {
                "request": request,
                "tenant": tenant,
                "verification_id": verification_id,
                "email": email,
                "error": message,
            })

        db.commit()
        return RedirectResponse(
            url=f"{_base}/{tenant_id}/unsubscribe/result?email={email}",
            status_code=303
        )

    except Exception as e:
        db.rollback()
        logger.error(f"구독 해지 인증 오류: {e}")
        return templates.TemplateResponse(resolve_template(tenant_id, "unsubscribe_verify.html"), {
            "request": request,
            "tenant": tenant,
            "verification_id": verification_id,
            "email": email,
            "error": "오류가 발생했습니다.",
        })
    finally:
        db.close()


@app.get("/{tenant_id}/unsubscribe/result", response_class=HTMLResponse)
async def unsubscribe_result_page(request: Request, tenant_id: str, email: str = ""):
    """구독 해지 완료 페이지"""
    tenant = get_tenant_or_404(tenant_id)
    return templates.TemplateResponse(resolve_template(tenant_id, "unsubscribe_result.html"), {
        "request": request,
        "tenant": tenant,
        "email": email,
    })


@app.get("/{tenant_id}/unsubscribe/token/{token}", response_class=HTMLResponse)
async def unsubscribe_by_token(request: Request, tenant_id: str, token: str):
    """토큰 기반 구독 해지 (이메일 링크)"""
    tenant = get_tenant_or_404(tenant_id)

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        success, message, email = subscription_manager.unsubscribe_by_token(db, token)

        if not success:
            return templates.TemplateResponse(resolve_template(tenant_id, "unsubscribe_result.html"), {
                "request": request,
                "tenant": tenant,
                "error": message,
            })

        db.commit()
        return templates.TemplateResponse(resolve_template(tenant_id, "unsubscribe_result.html"), {
            "request": request,
            "tenant": tenant,
            "email": email,
        })

    except Exception as e:
        db.rollback()
        logger.error(f"토큰 기반 구독 해지 오류: {e}")
        return templates.TemplateResponse(resolve_template(tenant_id, "unsubscribe_result.html"), {
            "request": request,
            "tenant": tenant,
            "error": "오류가 발생했습니다.",
        })
    finally:
        db.close()


# ==================== 구독 설정 (페르소나 관리) ====================

@app.get("/{tenant_id}/preferences/{token}", response_class=HTMLResponse)
async def preferences_form(request: Request, tenant_id: str, token: str):
    """구독 설정 페이지 — 페르소나·관심 알러젠 변경 (구독 해지 토큰으로 식별)"""
    tenant = get_tenant_or_404(tenant_id)
    personas = await persona_client.get_personas()

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        subscriber = SubscriberRepository.get_by_unsubscribe_token(db, token)
        if not subscriber or subscriber.tenant_id != tenant_id:
            return templates.TemplateResponse(resolve_template(tenant_id, "preferences.html"), {
                "request": request,
                "tenant": tenant,
                "error": "유효하지 않은 링크이거나 구독이 해지된 상태입니다.",
            })

        try:
            selected_interests = json.loads(subscriber.interests or "[]")
        except Exception:
            selected_interests = []

        return templates.TemplateResponse(resolve_template(tenant_id, "preferences.html"), {
            "request": request,
            "tenant": tenant,
            "token": token,
            "email": subscriber.email,
            "personas": personas,
            "interest_allergens": INTEREST_ALLERGENS if personas else [],
            "persona_code": subscriber.persona_code or "",
            "selected_interests": selected_interests,
        })
    finally:
        db.close()


@app.post("/{tenant_id}/preferences/{token}", response_class=HTMLResponse)
async def preferences_submit(
    request: Request,
    tenant_id: str,
    token: str,
    persona_code: str = Form(default=""),
    interests: list[str] = Form(default=[]),
):
    """구독 설정 저장 — 페르소나·관심 알러젠"""
    tenant = get_tenant_or_404(tenant_id)
    personas = await persona_client.get_personas()

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        subscriber = SubscriberRepository.get_by_unsubscribe_token(db, token)
        if not subscriber or subscriber.tenant_id != tenant_id:
            return templates.TemplateResponse(resolve_template(tenant_id, "preferences.html"), {
                "request": request,
                "tenant": tenant,
                "error": "유효하지 않은 링크이거나 구독이 해지된 상태입니다.",
            })

        # 화이트리스트 검증 — 카탈로그/정적 리스트에 있는 code 만 수용
        valid_persona_codes = {p.get("code") for p in personas}
        valid_interest_codes = {a["code"] for a in INTEREST_ALLERGENS}
        clean_persona = persona_code if persona_code in valid_persona_codes else ""
        clean_interests = [i for i in interests if i in valid_interest_codes]
        depth = (persona_default_depth(personas, clean_persona)
                 if clean_persona else "practical")

        SubscriberRepository.update_persona(
            db, subscriber.id,
            persona_code=clean_persona,   # "" → NULL (일반 구독자 = patient 폴백)
            depth_level=depth,
            interests=clean_interests,
        )
        db.commit()

        return templates.TemplateResponse(resolve_template(tenant_id, "preferences.html"), {
            "request": request,
            "tenant": tenant,
            "token": token,
            "email": subscriber.email,
            "personas": personas,
            "interest_allergens": INTEREST_ALLERGENS if personas else [],
            "persona_code": clean_persona,
            "selected_interests": clean_interests,
            "saved": True,
        })
    except Exception as e:
        db.rollback()
        logger.error(f"구독 설정 저장 오류: {e}")
        return templates.TemplateResponse(resolve_template(tenant_id, "preferences.html"), {
            "request": request,
            "tenant": tenant,
            "token": token,
            "personas": personas,
            "interest_allergens": INTEREST_ALLERGENS if personas else [],
            "persona_code": persona_code,
            "selected_interests": interests,
            "error": "오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
        })
    finally:
        db.close()


# ==================== 서버 실행 ====================

def run_server():
    """웹 서버 실행"""
    import uvicorn

    logger.info(f"웹 서버 시작: http://{settings.web_host}:{settings.web_port}")
    uvicorn.run(
        app,
        host=settings.web_host,
        port=settings.web_port,
        log_level="info"
    )
