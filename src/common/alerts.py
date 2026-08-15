"""운영 경보 채널 — Slack Incoming Webhook.

배경(설계 진단 P2): 2026년의 운영 사고 5건은 모두 "성공처럼 보이는 무음
실패"였고, 기존 경보(관리자 이메일)는 발송 인프라(Gmail) 자체가 죽는
시나리오에서 함께 죽는다. Slack webhook 은 발송 인프라와 독립된 경보 경로다.

- OPS_SLACK_WEBHOOK_URL 미설정 시 비활성 (warning 로그로만 남김)
- fire-and-forget: 경보 실패가 발송 파이프라인을 막으면 안 되므로 절대
  예외를 전파하지 않는다
- 스케줄러(동기) 컨텍스트에서 호출되므로 동기 httpx 사용, 짧은 타임아웃
"""

import logging

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_TIMEOUT_SEC = 5.0


def send_ops_alert(
    title: str,
    detail: str = "",
    tenant_id: str = "",
) -> bool:
    """운영 경보 발송. 성공 여부 반환 (실패해도 예외 없음).

    Args:
        title: 한 줄 요약 (예: "stale-cache 경보 발동")
        detail: 부가 정보 (수치·원인 등)
        tenant_id: 관련 테넌트 (없으면 플랫폼 전역)
    """
    prefix = f"[{tenant_id}] " if tenant_id else ""
    text = f":rotating_light: *NewsLetterPlatform* — {prefix}{title}"
    if detail:
        text += f"\n{detail}"

    webhook = settings.ops_slack_webhook_url
    if not webhook:
        logger.warning("OPS_SLACK_WEBHOOK_URL 미설정 — 경보 미발송: %s", title)
        return False

    try:
        resp = httpx.post(
            webhook, json={"text": text},
            timeout=_TIMEOUT_SEC, trust_env=False,
        )
        if resp.status_code == 200:
            logger.info("운영 경보 발송: %s", title)
            return True
        logger.warning(
            "운영 경보 실패 (HTTP %s): %s", resp.status_code, title
        )
        return False
    except Exception as e:
        logger.warning("운영 경보 발송 오류: %s (%s)", title, e)
        return False
