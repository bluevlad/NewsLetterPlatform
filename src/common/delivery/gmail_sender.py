"""
Gmail SMTP 이메일 발송 모듈
"""

import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from dataclasses import dataclass
from typing import Optional, List

from ...config import settings

logger = logging.getLogger(__name__)


@dataclass
class SendResult:
    """발송 결과"""
    recipient: str
    success: bool
    error_message: Optional[str] = None


class GmailSender:
    """Gmail SMTP 이메일 발송기"""

    SMTP_SERVER = "smtp.gmail.com"
    SMTP_PORT = 587

    def __init__(
        self,
        sender_email: str = None,
        app_password: str = None
    ):
        self.auth_email = sender_email or settings.gmail_address
        self.app_password = app_password or settings.gmail_app_password
        self.sender_email = settings.gmail_sender_address or self.auth_email

        if not self.auth_email or not self.app_password:
            logger.warning(
                "Gmail 설정이 완료되지 않았습니다. "
                ".env 파일에 GMAIL_ADDRESS와 GMAIL_APP_PASSWORD를 설정하세요."
            )

    @property
    def is_configured(self) -> bool:
        """Gmail 설정 완료 여부"""
        return bool(self.auth_email and self.app_password)

    def send(
        self,
        recipient: str,
        subject: str,
        html_content: str,
        sender_name: str = "NewsLetterPlatform"
    ) -> SendResult:
        """이메일 발송"""
        if not self.is_configured:
            return SendResult(
                recipient=recipient,
                success=False,
                error_message="Gmail 설정이 완료되지 않았습니다."
            )

        try:
            message = MIMEMultipart("alternative")
            message["Subject"] = Header(subject, "utf-8")
            message["From"] = f"{sender_name} <{self.sender_email}>"
            message["To"] = recipient
            message["Reply-To"] = self.sender_email
            message["Return-Path"] = self.sender_email

            html_part = MIMEText(html_content, "html", "utf-8")
            message.attach(html_part)

            with smtplib.SMTP(self.SMTP_SERVER, self.SMTP_PORT) as server:
                server.starttls()
                server.login(self.auth_email, self.app_password)
                server.sendmail(
                    self.sender_email,
                    recipient,
                    message.as_string()
                )

            logger.info(f"이메일 발송 성공: {recipient}")
            return SendResult(recipient=recipient, success=True)

        except smtplib.SMTPAuthenticationError:
            error_msg = "Gmail 인증 실패. 앱 비밀번호를 확인하세요."
            logger.error(f"이메일 발송 실패: {error_msg}")
            return SendResult(recipient=recipient, success=False, error_message=error_msg)

        except smtplib.SMTPRecipientsRefused:
            error_msg = f"수신자 거부: {recipient}"
            logger.error(f"이메일 발송 실패: {error_msg}")
            return SendResult(recipient=recipient, success=False, error_message=error_msg)

        except Exception as e:
            error_msg = str(e)
            logger.error(f"이메일 발송 실패: {error_msg}")
            return SendResult(recipient=recipient, success=False, error_message=error_msg)

    def send_batch(
        self,
        recipients: List[str],
        subject: str,
        html_content: str,
        sender_name: str = "NewsLetterPlatform"
    ) -> List[SendResult]:
        """다수 수신자에게 일괄 발송"""
        results = []
        for recipient in recipients:
            result = self.send(recipient, subject, html_content, sender_name)
            results.append(result)

        success_count = sum(1 for r in results if r.success)
        logger.info(f"일괄 발송 완료: {success_count}/{len(recipients)} 성공")

        return results


_sender: Optional[GmailSender] = None


def get_sender() -> GmailSender:
    global _sender
    if _sender is None:
        _sender = GmailSender()
    return _sender
