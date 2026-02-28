"""
Admin 인증 모듈 - 세션 기반 인증 + 로그인/로그아웃
"""

import secrets
import time
import logging

from fastapi import APIRouter, Request, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from ...config import settings
from ..shared import templates

logger = logging.getLogger(__name__)

router = APIRouter()

# in-memory 세션 저장소: {token: expiry_timestamp}
_sessions: dict[str, float] = {}


def create_session() -> str:
    """새 세션 생성, 토큰 반환"""
    token = secrets.token_urlsafe(32)
    expiry = time.time() + settings.admin_session_hours * 3600
    _sessions[token] = expiry
    return token


def validate_session(token: str) -> bool:
    """세션 유효성 검증"""
    if not token or token not in _sessions:
        return False
    if time.time() > _sessions[token]:
        del _sessions[token]
        return False
    return True


def delete_session(token: str) -> None:
    """세션 삭제"""
    _sessions.pop(token, None)


def require_admin(request: Request) -> bool:
    """인증 확인, 미인증 시 False 반환"""
    token = request.cookies.get("admin_session")
    return validate_session(token)


def get_admin_or_redirect(request: Request):
    """인증 확인, 미인증 시 리다이렉트 응답 반환"""
    if not require_admin(request):
        return RedirectResponse(url="/admin/login", status_code=303)
    return None


@router.get("/admin/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """로그인 페이지"""
    if require_admin(request):
        return RedirectResponse(url="/admin", status_code=303)
    return templates.TemplateResponse("admin/login.html", {
        "request": request,
    })


@router.post("/admin/login")
async def login_submit(request: Request, password: str = Form(...)):
    """로그인 처리"""
    if not settings.admin_password:
        return templates.TemplateResponse("admin/login.html", {
            "request": request,
            "error": "관리자 비밀번호가 설정되지 않았습니다. ADMIN_PASSWORD 환경변수를 설정해주세요.",
        })

    if secrets.compare_digest(password, settings.admin_password):
        token = create_session()
        response = RedirectResponse(url="/admin", status_code=303)
        response.set_cookie(
            key="admin_session",
            value=token,
            httponly=True,
            samesite="lax",
            max_age=settings.admin_session_hours * 3600,
        )
        logger.info("Admin 로그인 성공")
        return response

    logger.warning("Admin 로그인 실패: 잘못된 비밀번호")
    return templates.TemplateResponse("admin/login.html", {
        "request": request,
        "error": "비밀번호가 일치하지 않습니다.",
    })


@router.post("/admin/logout")
async def logout(request: Request):
    """로그아웃"""
    token = request.cookies.get("admin_session")
    if token:
        delete_session(token)
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie("admin_session")
    return response
