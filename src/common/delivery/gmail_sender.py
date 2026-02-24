"""
Gmail SMTP 이메일 발송 모듈
"""

import logging
import smtplib
import time
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
        self.sender_email = sender_email or settings.gmail_address
        self.app_password = app_password or settings.gmail_app_password

        if not self.sender_email or not self.app_password:
            logger.warning(
                "Gmail 설정이 완료되지 않았습니다. "
                ".env 파일에 GMAIL_ADDRESS와 GMAIL_APP_PASSWORD를 설정하세요."
            )

    @property
    def is_configured(self) -> bool:
        """Gmail 설정 완료 여부"""
        return bool(self.sender_email and self.app_password)

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
            safe_sender_name = sender_name.replace('\r', '').replace('\n', '').replace('\x00', '')
            message["From"] = f"{safe_sender_name} <{self.sender_email}>"
            message["To"] = recipient

            html_part = MIMEText(html_content, "html", "utf-8")
            message.attach(html_part)

            with smtplib.SMTP(self.SMTP_SERVER, self.SMTP_PORT) as server:
                server.starttls()
                server.login(self.sender_email, self.app_password)
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

    def _connect(self) -> smtplib.SMTP:
        """SMTP 서버 연결 및 인증"""
        server = smtplib.SMTP(self.SMTP_SERVER, self.SMTP_PORT)
        server.starttls()
        server.login(self.sender_email, self.app_password)
        return server

    def _send_single(self, server: smtplib.SMTP, msg: dict) -> SendResult:
        """단일 메일 발송 (이미 연결된 SMTP 서버 사용)"""
        recipient = msg["recipient"]
        try:
            message = MIMEMultipart("alternative")
            message["Subject"] = Header(msg["subject"], "utf-8")
            safe_sender_name = msg.get("sender_name", "NewsLetterPlatform").replace('\r', '').replace('\n', '').replace('\x00', '')
            message["From"] = f"{safe_sender_name} <{self.sender_email}>"
            message["To"] = recipient

            html_part = MIMEText(msg["html_content"], "html", "utf-8")
            message.attach(html_part)

            server.sendmail(self.sender_email, recipient, message.as_string())
            logger.info(f"이메일 발송 성공: {recipient}")
            return SendResult(recipient=recipient, success=True)
        except smtplib.SMTPRecipientsRefused:
            error_msg = f"수신자 거부: {recipient}"
            logger.error(f"이메일 발송 실패: {error_msg}")
            return SendResult(recipient=recipient, success=False, error_message=error_msg)
        except smtplib.SMTPServerDisconnected:
            raise  # 호출자가 재연결 처리
        except Exception as e:
            error_msg = str(e)
            logger.error(f"이메일 발송 실패 ({recipient}): {error_msg}")
            return SendResult(recipient=recipient, success=False, error_message=error_msg)

    def send_batch_efficient(
        self,
        messages: List[dict],
        batch_size: int = 10,
        delay: float = 0.5
    ) -> List[SendResult]:
        """SMTP 커넥션 재사용 배치 발송

        Args:
            messages: [{"recipient": str, "subject": str, "html_content": str, "sender_name": str}]
            batch_size: 스로틀링 딜레이 간격 (기본 10건)
            delay: 배치 간 딜레이 초 (기본 0.5초)
        """
        if not self.is_configured:
            return [
                SendResult(recipient=m["recipient"], success=False,
                           error_message="Gmail 설정이 완료되지 않았습니다.")
                for m in messages
            ]

        if not messages:
            return []

        results = []
        try:
            server = self._connect()
        except Exception as e:
            logger.error(f"SMTP 연결 실패: {e}")
            return [
                SendResult(recipient=m["recipient"], success=False, error_message=f"SMTP 연결 실패: {e}")
                for m in messages
            ]

        for i, msg in enumerate(messages):
            try:
                result = self._send_single(server, msg)
            except smtplib.SMTPServerDisconnected:
                logger.warning("SMTP 연결 끊김, 재연결 시도...")
                try:
                    server = self._connect()
                    result = self._send_single(server, msg)
                except Exception as e:
                    result = SendResult(
                        recipient=msg["recipient"], success=False,
                        error_message=f"재연결 후 발송 실패: {e}"
                    )
            results.append(result)

            if (i + 1) % batch_size == 0 and (i + 1) < len(messages):
                time.sleep(delay)

        try:
            server.quit()
        except Exception:
            pass

        success_count = sum(1 for r in results if r.success)
        logger.info(f"배치 발송 완료: {success_count}/{len(messages)} 성공")
        return results

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
