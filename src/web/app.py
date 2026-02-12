"""
NewsLetterPlatform 웹 애플리케이션
멀티테넌트 이메일 인증 기반 구독 시스템
"""

import logging
import threading
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import settings
from ..common.database.repository import get_session_factory
from ..common.subscription.manager import SubscriptionManager
from ..common.subscription.email_service import send_verification_email
from ..common.scheduler.jobs import send_welcome_newsletter
from ..tenant.registry import get_registry

logger = logging.getLogger(__name__)

# FastAPI 앱 생성
app = FastAPI(
    title="NewsLetterPlatform",
    description="멀티테넌트 뉴스레터 통합 플랫폼",
    version="1.0.0"
)

class CSRFOriginCheckMiddleware(BaseHTTPMiddleware):
    """CSRF 방지: POST 요청의 Origin/Referer가 허용된 호스트인지 검증"""

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
                if parsed.hostname not in allowed_hosts:
                    logger.warning("CSRF check failed: origin=%s", origin)
                    raise HTTPException(status_code=403, detail="Forbidden: invalid origin")
        return await call_next(request)

# CSRF 미들웨어 적용
app.add_middleware(CSRFOriginCheckMiddleware)

# 웹 페이지 템플릿
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

# 구독 매니저
subscription_manager = SubscriptionManager()


def resolve_template(tenant_id: str, template_name: str) -> str:
    """테넌트별 템플릿 오버라이드 지원

    overrides/{tenant_id}/{template_name} 파일이 존재하면 우선 사용,
    없으면 기본 템플릿 반환.
    """
    override_path = templates_dir / "overrides" / tenant_id / template_name
    if override_path.exists():
        return f"overrides/{tenant_id}/{template_name}"
    return template_name


def get_db():
    """데이터베이스 세션"""
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_tenant_or_404(tenant_id: str):
    """테넌트 조회, 없으면 404"""
    if not tenant_id or len(tenant_id) > 64 or not tenant_id.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="잘못된 테넌트 ID 형식입니다")
    registry = get_registry()
    tenant = registry.get(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"테넌트를 찾을 수 없습니다: {tenant_id}")
    return tenant


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
    return templates.TemplateResponse(resolve_template(tenant_id, "subscribe.html"), {
        "request": request,
        "tenant": tenant,
    })


@app.post("/{tenant_id}/subscribe", response_class=HTMLResponse)
async def subscribe_submit(
    request: Request,
    tenant_id: str,
    email: str = Form(...),
    name: str = Form(default="")
):
    """구독 신청 처리 - 인증코드 발송"""
    tenant = get_tenant_or_404(tenant_id)

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        success, code_or_msg, verification_id = subscription_manager.request_subscribe(
            db, tenant_id, email, name
        )

        if not success:
            return templates.TemplateResponse(resolve_template(tenant_id, "subscribe.html"), {
                "request": request,
                "tenant": tenant,
                "error": code_or_msg,
                "email": email,
                "name": name,
            })

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
                url=f"/{tenant_id}/verify/{verification_id}?email={email.strip().lower()}",
                status_code=303
            )
        else:
            return templates.TemplateResponse(resolve_template(tenant_id, "subscribe.html"), {
                "request": request,
                "tenant": tenant,
                "error": "이메일 발송에 실패했습니다. 잠시 후 다시 시도해주세요.",
                "email": email,
                "name": name,
            })

    except Exception as e:
        db.rollback()
        logger.error(f"구독 신청 처리 오류: {e}")
        return templates.TemplateResponse(resolve_template(tenant_id, "subscribe.html"), {
            "request": request,
            "tenant": tenant,
            "error": "오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
            "email": email,
            "name": name,
        })
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
            url=f"/{tenant_id}/result?email={email}",
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
async def unsubscribe_submit(
    request: Request,
    tenant_id: str,
    email: str = Form(...)
):
    """구독 해지 신청 처리 - 인증코드 발송"""
    tenant = get_tenant_or_404(tenant_id)

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
                url=f"/{tenant_id}/unsubscribe/verify/{verification_id}?email={email.strip().lower()}",
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
            url=f"/{tenant_id}/unsubscribe/result?email={email}",
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
