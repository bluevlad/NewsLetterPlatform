"""
Admin 인증 모듈 - 세션 기반 인증 + 로그인/로그아웃 + Google Sign-In
"""

import secrets
import logging

from fastapi import APIRouter, Request, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from ...config import settings
from ..shared import templates

# 리버스 프록시 base path prefix
_base = settings.root_path

logger = logging.getLogger(__name__)

router = APIRouter()

# 세션 서명 시크릿 — settings.session_secret(안정값) 우선.
# 미설정 시 기동 시 임의 생성(과거 in-memory 와 동일하게 재시작 시 세션 소실)
# 하되 경고. 운영은 SESSION_SECRET 설정 필수.
if settings.session_secret:
    _session_secret = settings.session_secret
else:
    _session_secret = secrets.token_urlsafe(32)
    logger.warning(
        "SESSION_SECRET 미설정 — 임의 키 사용. 재시작 시 관리자 세션이 소실되고 "
        "다중 워커에서 세션이 공유되지 않습니다. 운영에서는 SESSION_SECRET 을 설정하세요."
    )

# 상태 비저장(stateless) 서명 토큰: 서버측 저장소 없이 서명+만료로 검증.
# → 재시작·다중 워커에서 세션 유지(기존 in-memory dict 의 소실/불일치 문제 해결).
_serializer = URLSafeTimedSerializer(_session_secret, salt="admin-session")


def create_session() -> str:
    """새 세션 토큰 생성(서명된 토큰). 서버측 저장 없음."""
    return _serializer.dumps({"t": "admin"})


def validate_session(token: str) -> bool:
    """세션 유효성 검증 — 서명 + 만료(admin_session_hours) 확인."""
    if not token:
        return False
    try:
        _serializer.loads(token, max_age=settings.admin_session_hours * 3600)
        return True
    except (BadSignature, SignatureExpired):
        return False


def delete_session(token: str) -> None:
    """로그아웃 — stateless 이므로 서버측 상태는 없고 쿠키 삭제로 처리한다.

    (토큰 즉시 무효화가 필요하면 후속으로 denylist 를 도입한다. 현재는 만료 +
    쿠키 삭제로 관리한다.)
    """
    return None


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
        "password_login_enabled": bool(settings.admin_password),
    })
    # Google Identity Services 팝업이 postMessage로 credential을 전달할 수 있도록 허용
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
    return response


@router.post("/admin/login")
async def login_submit(request: Request, password: str = Form(...)):
    """로그인 처리 (비밀번호) — ADMIN_PASSWORD 미설정 시 사용 불가."""
    if not settings.admin_password:
        # 비밀번호 로그인 비활성화 — Google Sign-In 단일 진입점으로 운영
        logger.warning("Admin 비밀번호 로그인 시도 차단: ADMIN_PASSWORD 미설정")
        return templates.TemplateResponse(
            "admin/login.html",
            {
                "request": request,
                "error": "비밀번호 로그인은 비활성화되어 있습니다. Google 로그인으로 진행해주세요.",
                "google_oauth_enabled": _is_google_oauth_configured(),
                "google_client_id": settings.google_client_id if _is_google_oauth_configured() else "",
                "password_login_enabled": False,
            },
            status_code=403,
        )

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
        "password_login_enabled": True,
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
        # 서버-구글 시계 미세 드리프트로 인한 "Token used too early" 401 방지
        # (구글 권장, 최대 60s). 기본값 0 은 실운영에 너무 엄격.
        clock_skew_in_seconds=10,
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
