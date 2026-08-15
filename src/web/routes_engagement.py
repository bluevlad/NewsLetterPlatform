"""Engagement 수집 엔드포인트 — open pixel / 클릭 리다이렉트 / 피드백 (P2).

  GET /e/o/{tenant_id}/{subscriber_id}.gif?t=  → open 기록 + 1×1 gif
  GET /e/c/{tenant_id}/{subscriber_id}?u=&t=   → click 기록 + 302 리다이렉트
  GET /e/f/{tenant_id}/{subscriber_id}?v=&t=   → feedback 기록 + 안내 페이지

보안 원칙 (Freshness 트랙 L 불변식):
  - 모든 요청은 HMAC 서명(t) 검증 — 실패 시 이벤트 미기록·정보 미노출
  - 클릭 서명은 대상 URL 을 포함하므로 open-redirect 로 악용 불가
  - URL 에 이메일 등 PII 없음 (subscriber_id 정수만)
메일 스캐너가 링크를 선탐색해도 open/click 은 통계 노이즈일 뿐 상태 변경이
없고, feedback 은 GET 이지만 구독 상태를 바꾸지 않는 저위험 기록이다.
"""

import base64
import logging
from urllib.parse import unquote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from slowapi import Limiter

from ..common.database.repository import get_session_factory
from ..common.database.repo_engagement import EngagementEventRepository
from ..common.engagement import verify
from ..common.security import get_client_ip
from ..config import settings

logger = logging.getLogger(__name__)

router = APIRouter()
limiter = Limiter(key_func=get_client_ip)

# 1×1 투명 GIF
_PIXEL = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)

_FEEDBACK_THANKS = """<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>의견이 전달되었습니다</title></head>
<body style="font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;
             display:flex;align-items:center;justify-content:center;
             min-height:80vh;margin:0;background:#faf9f6;color:#292420;">
<div style="text-align:center;padding:32px;">
  <div style="font-size:44px;margin-bottom:12px;">{icon}</div>
  <h2 style="margin:0 0 8px;">의견이 전달되었습니다</h2>
  <p style="color:#857b70;margin:0;">더 나은 뉴스레터를 만드는 데 사용됩니다. 감사합니다!</p>
</div></body></html>"""


def _record(tenant_id: str, subscriber_id: int, event_type: str, **kwargs):
    db = get_session_factory()()
    try:
        EngagementEventRepository.record(
            db, tenant_id, subscriber_id, event_type, **kwargs
        )
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("engagement 기록 실패 (%s): %s", event_type, e)
    finally:
        db.close()


@router.get("/e/o/{tenant_id}/{subscriber_id}.gif")
async def track_open(tenant_id: str, subscriber_id: int, t: str = ""):
    """오픈 픽셀 — 서명 유효 시에만 기록. 항상 gif 반환 (정보 미노출)."""
    if settings.engagement_enabled and verify("o", tenant_id, subscriber_id, t):
        _record(tenant_id, subscriber_id, "open")
    return Response(
        content=_PIXEL, media_type="image/gif",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get("/e/c/{tenant_id}/{subscriber_id}")
async def track_click(tenant_id: str, subscriber_id: int,
                      u: str = "", t: str = ""):
    """클릭 리다이렉트 — 서명이 URL 을 포함해 검증하므로 임의 URL 불가."""
    url = unquote(u or "")
    if (
        settings.engagement_enabled
        and url.startswith(("http://", "https://"))
        and verify("c", tenant_id, subscriber_id, t, extra=url)
    ):
        _record(tenant_id, subscriber_id, "click", target_url=url[:1000])
        return RedirectResponse(url, status_code=302)
    # 서명 불일치 — 리다이렉트하지 않음 (open-redirect 차단)
    return HTMLResponse(
        "<p style='font-family:sans-serif'>유효하지 않은 링크입니다.</p>",
        status_code=400,
    )


@router.get("/e/f/{tenant_id}/{subscriber_id}", response_class=HTMLResponse)
@limiter.limit("30/hour")
async def track_feedback(request: Request, tenant_id: str,
                         subscriber_id: int, v: str = "", t: str = ""):
    """피드백(👍/👎) — 서명 검증 후 기록, 감사 페이지 반환."""
    if v not in ("up", "down"):
        return HTMLResponse("잘못된 요청입니다.", status_code=400)
    if not (
        settings.engagement_enabled
        and verify("f", tenant_id, subscriber_id, t, extra=v)
    ):
        return HTMLResponse(
            "<p style='font-family:sans-serif'>유효하지 않은 링크입니다.</p>",
            status_code=400,
        )
    _record(tenant_id, subscriber_id, "feedback", feedback_value=v)
    icon = "👍" if v == "up" else "🙏"
    return HTMLResponse(_FEEDBACK_THANKS.format(icon=icon))
