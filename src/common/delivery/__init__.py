"""이메일 발송 패키지"""

from .gmail_sender import GmailSender, SendResult, get_sender

__all__ = ["GmailSender", "SendResult", "get_sender"]
