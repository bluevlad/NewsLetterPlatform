"""Engagement 링크 서명·생성 — open pixel / 클릭 리다이렉트 / 피드백 (P2).

Freshness 플랜 트랙 L 의 불변식을 따른다:
  - 토큰은 HMAC 서명 (session_secret 기반) — 위조 불가
  - URL 에 이메일 등 PII 미포함 (subscriber_id 정수 + 서명만)
  - 서명 검증 실패는 조용히 무시 (이벤트 미기록, 정보 미노출)

SESSION_SECRET 미설정(개발 환경) 시 프로세스 간 서명이 어긋나므로
링크 생성이 비활성화된다 (빈 문자열 반환 → 템플릿 placeholder 무해 치환).
"""

import hashlib
import hmac
import html as _htmllib
import logging
import re
from urllib.parse import quote

from ..config import settings

logger = logging.getLogger(__name__)

_SIG_LEN = 32  # sha256 hex truncation (16 bytes)


def _secret() -> bytes:
    return settings.session_secret.encode("utf-8") if settings.session_secret else b""


def engagement_available() -> bool:
    """엔게이지먼트 링크 생성 가능 여부 (마스터 스위치 + 시크릿)."""
    return bool(settings.engagement_enabled and settings.session_secret)


def sign(kind: str, tenant_id: str, subscriber_id: int, extra: str = "") -> str:
    """이벤트 링크 서명. kind: 'o'(open) | 'c'(click) | 'f'(feedback)."""
    msg = f"{kind}:{tenant_id}:{subscriber_id}:{extra}".encode("utf-8")
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()[:_SIG_LEN]


def verify(kind: str, tenant_id: str, subscriber_id: int,
           token: str, extra: str = "") -> bool:
    if not settings.session_secret or not token:
        return False
    expected = sign(kind, tenant_id, subscriber_id, extra)
    return hmac.compare_digest(expected, token)


def make_open_pixel(tenant_id: str, subscriber_id: int) -> str:
    """오픈 추적 1×1 픽셀 <img> 태그. 비활성 시 빈 문자열."""
    if not engagement_available():
        return ""
    t = sign("o", tenant_id, subscriber_id)
    url = (
        f"{settings.web_base_url}/e/o/{tenant_id}/{subscriber_id}.gif?t={t}"
    )
    return (
        f'<img src="{url}" width="1" height="1" alt="" '
        f'style="display:none;max-height:0;overflow:hidden;">'
    )


def make_click_url(tenant_id: str, subscriber_id: int, url: str) -> str:
    """클릭 추적 리다이렉트 URL. 비활성 시 원본 URL 그대로."""
    if not engagement_available():
        return url
    t = sign("c", tenant_id, subscriber_id, extra=url)
    return (
        f"{settings.web_base_url}/e/c/{tenant_id}/{subscriber_id}"
        f"?u={quote(url, safe='')}&t={t}"
    )


def make_feedback_url(tenant_id: str, subscriber_id: int, value: str) -> str:
    """피드백(up/down) URL. 비활성 시 '#'."""
    if not engagement_available():
        return "#"
    t = sign("f", tenant_id, subscriber_id, extra=value)
    return (
        f"{settings.web_base_url}/e/f/{tenant_id}/{subscriber_id}"
        f"?v={value}&t={t}"
    )


_HREF_RE = re.compile(r'href="(https?://[^"]+)"')


def rewrite_click_links(html: str, tenant_id: str, subscriber_id: int) -> str:
    """본문 외부 링크를 클릭 추적 리다이렉트로 재작성 (기본 off, env 게이트).

    - 자사(web_base_url) 링크는 제외 — 구독 해지·페르소나 딥링크 보호
    - href 속성값의 HTML 엔티티(&amp;)를 풀어 서명·인코딩 후 재이스케이프
    """
    if not (
        engagement_available()
        and settings.engagement_click_tracking_enabled
    ):
        return html

    base = settings.web_base_url

    def _sub(m: "re.Match") -> str:
        raw = m.group(1)
        url = _htmllib.unescape(raw)
        if url.startswith(base):
            return m.group(0)
        tracked = make_click_url(tenant_id, subscriber_id, url)
        return f'href="{_htmllib.escape(tracked, quote=True)}"'

    return _HREF_RE.sub(_sub, html)
