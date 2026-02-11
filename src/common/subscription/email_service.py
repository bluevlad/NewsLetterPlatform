"""
구독 관련 이메일 발송 헬퍼
"""

import logging

from ..delivery.gmail_sender import get_sender
from ..template.renderer import get_renderer

logger = logging.getLogger(__name__)


def send_verification_email(
    tenant_name: str,
    tenant_subject_prefix: str,
    email: str,
    name: str,
    code: str,
    verification_type: str = "subscribe"
) -> bool:
    """인증코드 이메일 발송"""
    try:
        renderer = get_renderer()
        html_content = renderer.render_verification_email(
            tenant_name=tenant_name,
            email=email,
            name=name,
            code=code,
            verification_type=verification_type
        )

        if verification_type == "unsubscribe":
            subject = f"{tenant_subject_prefix} 구독 해지 인증코드: {code}"
        else:
            subject = f"{tenant_subject_prefix} 인증코드: {code}"

        sender = get_sender()
        result = sender.send(
            recipient=email,
            subject=subject,
            html_content=html_content,
            sender_name=tenant_name
        )

        return result.success
    except Exception as e:
        logger.error(f"인증 이메일 발송 실패: {e}")
        return False
