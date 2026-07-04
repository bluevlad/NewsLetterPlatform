"""구독 폼 어뷰즈 방어 — 공통 검증 함수

가이드: standards/services/newsletterplatform/SUBSCRIPTION_ABUSE_HARDENING.md
2026-05-02 시작된 Subscription Bombing 대응. 본 모듈은 시그니처 기반
정적 검증(role-account, 봇 이름 패턴, honeypot)과 외부 captcha(Turnstile)
검증을 제공한다.
"""

import re
import logging
from dataclasses import dataclass
from typing import Optional

import httpx
from fastapi import Request

from ...config import settings

logger = logging.getLogger(__name__)


# Role-account: 자동화/공유 메일함 패턴 — 인증 메일 폭격의 표적이 되는 주소
_ROLE_ACCOUNT_RE = re.compile(
    r"^(info|admin|administrator|support|noreply|no-reply|"
    r"postmaster|sales|contact|webmaster|root|abuse|"
    r"hr|jobs|press|marketing|billing|help|service|office)@",
    re.IGNORECASE,
)


# 봇 시그니처: 모음/자음 분포가 사람 이름으로 보기 어려운 무작위 lowercase 문자열
# 본 어뷰즈에서 관찰된 패턴: peswpnjvff / odktwhvrdu / nwhnmpdzmg 등 10자 무작위
_BOT_NAME_RE = re.compile(r"^[a-z]{8,12}$")
_VOWELS = set("aeiou")


@dataclass
class AbuseCheckResult:
    """검증 결과 — silent drop 여부와 사용자 메시지 분리"""
    blocked: bool
    reason: str = ""
    silent: bool = False  # True 이면 봇에게 차단 사실 노출 금지 (사용자 응답은 200)


def is_role_account(email: str) -> bool:
    """role-account 메일함 여부"""
    return bool(_ROLE_ACCOUNT_RE.match(email or ""))


def is_bot_name_pattern(name: str) -> bool:
    """이름 컬럼이 봇 자동 생성 무작위 문자열로 보이는지

    조건: 8–12자 소문자 only + 모음 비율 < 25% (정상 영문 이름은 보통 30%+)
    """
    if not name:
        return False
    name = name.strip().lower()
    if not _BOT_NAME_RE.match(name):
        return False
    vowel_count = sum(1 for c in name if c in _VOWELS)
    return (vowel_count / len(name)) < 0.25


def is_honeypot_filled(value: Optional[str]) -> bool:
    """honeypot 필드가 채워졌는지 — 사람은 비어있고 봇은 모든 필드를 채운다"""
    return bool(value and value.strip())


def get_client_ip(request: Request, trusted_hops: Optional[int] = None) -> str:
    """리버스 프록시 환경에서 실제 클라이언트 IP 추출.

    X-Forwarded-For 의 **맨 왼쪽**은 클라이언트가 임의로 위조할 수 있어
    (예: `X-Forwarded-For: 1.2.3.4` 를 그대로 주입) rate-limit 우회에 악용된다.
    우리 신뢰 프록시(nginx 게이트웨이)가 append 한 **오른쪽에서 trusted_hops
    번째** 값을 쓴다 — 이 위치는 클라이언트가 덮어쓸 수 없다.
    """
    hops = trusted_hops if trusted_hops is not None else getattr(settings, "trusted_proxy_hops", 1)
    hops = max(hops, 1)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            idx = min(hops, len(parts))
            return parts[-idx]
    if request.client:
        return request.client.host
    return "unknown"


async def verify_turnstile(token: str, secret: str, remote_ip: Optional[str] = None) -> bool:
    """Cloudflare Turnstile 토큰 검증

    secret 이 비어 있으면 검증 스킵 (env 미설정 시 비활성화 동작).
    """
    if not secret:
        return True  # disabled
    if not token:
        return False

    payload = {"secret": secret, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            resp = await client.post(
                "https://challenges.cloudflare.com/turnstile/v0/siteverify",
                data=payload,
            )
            data = resp.json()
            return bool(data.get("success"))
    except Exception as e:
        logger.error("Turnstile 검증 실패: %s", e)
        return False
