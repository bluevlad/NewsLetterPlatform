"""
Admin 인증 모듈 - 세션 기반 인증 + 로그인/로그아웃 + Google Sign-In
"""

import secrets
import time
import logging

from fastapi import APIRouter, Request, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from ...config import settings
from ..shared import templates

# 리버스 프록시 base path prefix
_base = settings.root_path

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
        return RedirectResponse(url=f"{_base}/admin/login", status_code=303)
    return None


def _is_google_oauth_configured() -> bool:
    """Google Sign-In 설정 여부 확인 (client_id만 필요)"""
    return bool(settings.google_client_id)


def _get_super_admin_emails() -> set[str]:
    """SUPER_ADMIN_EMAILS 환경변수를 set으로 반환"""
    if not settings.super_admin_emails:
        return set()
    return {
        email.strip().lower()
        for email in settings.super_admin_emails.split(",")
        if email.strip()
    }


def _set_session_cookie(response: Response, token: str) -> Response:
    """세션 쿠키 설정 (공통)"""
    response.set_cookie(
        key="admin_session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=settings.admin_session_hours * 3600,
    )
    return response


@router.get("/admin/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """로그인 페이지"""
    if require_admin(request):
        return RedirectResponse(url=f"{_base}/admin", status_code=303)

    error = request.query_params.get("error")
    error_messages = {
        "not_admin": "해당 Google 계정은 관리자로 등록되어 있지 않습니다.",
        "oauth_failed": "Google 로그인에 실패했습니다. 다시 시도해주세요.",
        "no_email": "Google 계정에서 이메일 정보를 가져올 수 없습니다.",
        "invalid_credential": "유효하지 않은 인증 정보입니다.",
    }

    response = templates.TemplateResponse("admin/login.html", {
        "request": request,
        "error": error_messages.get(error),
        "google_oauth_enabled": _is_google_oauth_configured(),
        "google_client_id": settings.google_client_id if _is_google_oauth_configured() else "",
    })
    # Google Identity Services 팝업이 postMessage로 credential을 전달할 수 있도록 허용
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
    return response


@router.post("/admin/login")
async def login_submit(request: Request, password: str = Form(...)):
    """로그인 처리 (비밀번호)"""
    if not settings.admin_password:
        return templates.TemplateResponse("admin/login.html", {
            "request": request,
            "error": "관리자 비밀번호가 설정되지 않았습니다. ADMIN_PASSWORD 환경변수를 설정해주세요.",
            "google_oauth_enabled": _is_google_oauth_configured(),
            "google_client_id": "",
        })

    if secrets.compare_digest(password, settings.admin_password):
        token = create_session()
        response = RedirectResponse(url=f"{_base}/admin", status_code=303)
        _set_session_cookie(response, token)
        logger.info("Admin 로그인 성공 (비밀번호)")
        return response

    logger.warning("Admin 로그인 실패: 잘못된 비밀번호")
    return templates.TemplateResponse("admin/login.html", {
        "request": request,
        "error": "비밀번호가 일치하지 않습니다.",
        "google_oauth_enabled": _is_google_oauth_configured(),
        "google_client_id": settings.google_client_id if _is_google_oauth_configured() else "",
    })


@router.post("/admin/logout")
async def logout(request: Request):
    """로그아웃"""
    token = request.cookies.get("admin_session")
    if token:
        delete_session(token)
    response = RedirectResponse(url=f"{_base}/admin/login", status_code=303)
    response.delete_cookie("admin_session")
    return response


# ==================== Google Sign-In (ID Token 검증) ====================

def _verify_google_id_token(credential: str) -> dict:
    """Google ID Token 검증 후 사용자 정보 반환"""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    idinfo = id_token.verify_oauth2_token(
        credential,
        google_requests.Request(),
        settings.google_client_id,
    )
    if idinfo["iss"] not in ("accounts.google.com", "https://accounts.google.com"):
        raise ValueError("Invalid issuer")
    return idinfo


@router.post("/admin/auth/google/verify")
async def google_verify(request: Request):
    """Google Sign-In ID Token 검증 → 세션 생성"""
    if not _is_google_oauth_configured():
        return JSONResponse({"error": "Google 로그인이 설정되지 않았습니다."}, status_code=400)

    body = await request.json()
    credential = body.get("credential", "")
    if not credential:
        return JSONResponse({"error": "credential이 없습니다."}, status_code=400)

    try:
        idinfo = _verify_google_id_token(credential)
    except Exception as e:
        logger.error("Google ID Token 검증 실패: %s", e)
        return JSONResponse({"error": "유효하지 않은 인증 정보입니다."}, status_code=401)

    email = idinfo.get("email", "").strip().lower()
    if not email:
        return JSONResponse({"error": "이메일 정보를 가져올 수 없습니다."}, status_code=400)

    admin_emails = _get_super_admin_emails()
    if admin_emails and email not in admin_emails:
        logger.warning("Google Sign-In 로그인 거부: %s (관리자 아님)", email)
        return JSONResponse({"error": "관리자 권한이 없는 계정입니다."}, status_code=403)

    # 관리자 확인됨 - 세션 생성
    session_token = create_session()
    response = JSONResponse({"redirect": f"{_base}/admin/"})
    _set_session_cookie(response, session_token)
    logger.info("Admin Google Sign-In 로그인 성공: %s", email)
    return response
