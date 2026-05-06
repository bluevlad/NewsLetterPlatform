"""Bounce Feedback Loop — Gmail IMAP에서 NDR 메시지를 수집/파싱

운영자 발신함(rainend00@gmail.com)에서 mailer-daemon/postmaster의 NDR을
주기적으로 가져와:
  1) 영구 실패(hard bounce, SMTP 5xx) 주소를 전 테넌트에서 비활성화
  2) bounce_log에 기록 → request_subscribe 진입 단계 사전 차단
  3) 처리한 NDR은 INBOX 라벨 제거(Gmail archive)하여 운영자 inbox에서 분리

가이드: standards/services/newsletterplatform/SUBSCRIPTION_ABUSE_HARDENING.md
"""

import imaplib
import email
import email.policy
import logging
import re
from email.message import Message
from typing import Optional, Tuple

from ...config import settings
from ..database.repository import (
    get_session, BounceLogRepository, SubscriberRepository
)

logger = logging.getLogger(__name__)


IMAP_SERVER = "imap.gmail.com"
IMAP_PORT = 993


# DSN(RFC 3464) Status 코드: 5.x.x = permanent, 4.x.x = transient
_STATUS_RE = re.compile(r"\b([245])\.\d+\.\d+\b")
_FINAL_RECIPIENT_RE = re.compile(
    r"^Final-Recipient:\s*(?:rfc822;|utf-8;)?\s*(\S+@\S+)",
    re.IGNORECASE | re.MULTILINE,
)
_DIAGNOSTIC_RE = re.compile(
    r"^Diagnostic-Code:\s*(?:smtp;)?\s*(\d{3}[ -]?[\d.]+\s+.+)",
    re.IGNORECASE | re.MULTILINE,
)
# fallback 본문 패턴 (Gmail 한글 NDR / Office 365 등)
_FALLBACK_EMAIL_RE = re.compile(
    r"\b([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b"
)
_FALLBACK_SMTP_CODE_RE = re.compile(
    r"\b(5\d{2}|4\d{2})[ -](?:[\d.]+\s+)?([^\n]{0,120})", re.IGNORECASE
)


def _classify_bounce(status_code: str) -> str:
    """Status 코드 첫자리 → 'hard'(5) | 'soft'(4) | 'unknown'"""
    m = _STATUS_RE.search(status_code or "")
    if m:
        return "hard" if m.group(1) == "5" else "soft"
    # fallback: SMTP 코드 첫자리
    m2 = re.search(r"\b([245])\d{2}\b", status_code or "")
    if m2:
        return "hard" if m2.group(1) == "5" else "soft"
    return "unknown"


def _parse_dsn_part(msg: Message) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """RFC 3464 message/delivery-status 파트 파싱

    Returns: (failed_email, status_code, diagnostic)
    """
    if not msg.is_multipart():
        return None, None, None
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype != "message/delivery-status":
            continue
        # message/delivery-status 는 Python email 라이브러리가 multipart 컨테이너로
        # 처리하므로 get_payload(decode=True)가 None을 반환한다. as_string으로
        # 원본 본문 텍스트를 추출 후 헤더를 정규식으로 파싱한다.
        try:
            body = part.as_string()
        except Exception:
            try:
                body = "\n".join(p.as_string() for p in part.walk() if p is not part)
            except Exception:
                continue
        if not body:
            continue

        recipient_match = _FINAL_RECIPIENT_RE.search(body)
        if not recipient_match:
            continue
        failed_email = recipient_match.group(1).strip().lower().rstrip(">").lstrip("<")

        status_match = re.search(r"^Status:\s*([\d.]+)", body, re.IGNORECASE | re.MULTILINE)
        status_code = status_match.group(1) if status_match else None

        diag_match = _DIAGNOSTIC_RE.search(body)
        diagnostic = diag_match.group(1).strip() if diag_match else None

        return failed_email, status_code, diagnostic

    return None, None, None


def _extract_text(msg: Message) -> str:
    """본문 text/plain 합쳐 반환 (fallback parsing 용)"""
    chunks = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        chunks.append(payload.decode("utf-8", errors="replace"))
                except Exception:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if isinstance(payload, bytes):
                chunks.append(payload.decode("utf-8", errors="replace"))
        except Exception:
            pass
    return "\n".join(chunks)


def parse_ndr(raw_bytes: bytes) -> Tuple[Optional[str], Optional[str], str, str]:
    """NDR 메시지 파싱

    Returns: (failed_email, smtp_code, bounce_type, diagnostic_excerpt)
    bounce_type: 'hard' | 'soft' | 'unknown'
    """
    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)

    # 1차: RFC 3464 DSN 파트 파싱
    failed_email, status_code, diagnostic = _parse_dsn_part(msg)
    if failed_email:
        bounce_type = _classify_bounce(status_code or (diagnostic or ""))
        return failed_email, status_code, bounce_type, (diagnostic or "")[:2000]

    # 2차: 본문 fallback 파싱 (Gmail 한글 NDR 등 DSN 누락 케이스)
    text = _extract_text(msg)
    if not text:
        return None, None, "unknown", ""

    # 본문 첫 번째 이메일 (운영자 자기 메일 제외)
    operator_email = (settings.gmail_address or "").strip().lower()
    candidates = [e.lower() for e in _FALLBACK_EMAIL_RE.findall(text)]
    failed_email = next(
        (e for e in candidates
         if e != operator_email
         and not e.startswith("mailer-daemon@")
         and not e.startswith("postmaster@")),
        None,
    )
    if not failed_email:
        return None, None, "unknown", text[:500]

    code_match = _FALLBACK_SMTP_CODE_RE.search(text)
    smtp_code = code_match.group(0).strip() if code_match else None
    bounce_type = _classify_bounce(smtp_code or "")
    # Gmail 한글: '주소를 찾을 수 없' = hard, '받은편지함 용량' = soft 단서
    if bounce_type == "unknown":
        if "찾을 수 없" in text or "Address not found" in text or "User unknown" in text.lower():
            bounce_type = "hard"
        elif "용량 초과" in text or "mailbox full" in text.lower() or "quota" in text.lower():
            bounce_type = "soft"

    return failed_email, smtp_code, bounce_type, text[:2000]


class BounceProcessor:
    """IMAP 폴링 기반 NDR 처리기"""

    NDR_SEARCH_CRITERIA = [
        '(FROM "mailer-daemon@")',
        '(FROM "postmaster@")',
        '(SUBJECT "Delivery Status Notification")',
        '(SUBJECT "Undeliverable")',
        '(SUBJECT "주소를 찾을 수 없")',
    ]

    def __init__(self, server: str = IMAP_SERVER, port: int = IMAP_PORT):
        self.server = server
        self.port = port
        self.email = settings.gmail_address
        self.password = settings.gmail_app_password

    def is_configured(self) -> bool:
        return bool(self.email and self.password)

    def process(self, since_days: int = 7) -> dict:
        """INBOX의 NDR을 수집/파싱 → DB 적재 → archive

        Returns: {processed, hard, soft, archived, errors}
        """
        if not self.is_configured():
            logger.warning("BounceProcessor: gmail 자격증명 미설정 — skip")
            return {"processed": 0, "hard": 0, "soft": 0, "archived": 0, "errors": 0}

        stats = {"processed": 0, "hard": 0, "soft": 0, "archived": 0, "errors": 0}
        try:
            with imaplib.IMAP4_SSL(self.server, self.port) as imap:
                imap.login(self.email, self.password)
                imap.select("INBOX")
                uids = self._search_ndr_uids(imap, since_days)
                logger.info("BounceProcessor: NDR 후보 %d건 발견", len(uids))

                for uid in uids:
                    try:
                        if self._process_one(imap, uid, stats):
                            self._archive(imap, uid)
                            stats["archived"] += 1
                    except Exception as e:
                        logger.exception("BounceProcessor: UID %s 처리 실패: %s", uid, e)
                        stats["errors"] += 1
                imap.logout()
        except Exception as e:
            logger.exception("BounceProcessor: IMAP 세션 실패: %s", e)
            stats["errors"] += 1

        logger.info("BounceProcessor 결과: %s", stats)
        return stats

    def _search_ndr_uids(self, imap: imaplib.IMAP4_SSL, since_days: int) -> list[bytes]:
        """다중 검색식 합집합 + 중복 제거"""
        from datetime import datetime, timedelta
        since_str = (datetime.utcnow() - timedelta(days=since_days)).strftime("%d-%b-%Y")
        seen = set()
        result = []
        for crit in self.NDR_SEARCH_CRITERIA:
            try:
                typ, data = imap.uid("SEARCH", None, f"SINCE {since_str}", crit)
                if typ != "OK" or not data or not data[0]:
                    continue
                for uid in data[0].split():
                    if uid not in seen:
                        seen.add(uid)
                        result.append(uid)
            except Exception as e:
                logger.warning("BounceProcessor: 검색 실패 (%s): %s", crit, e)
        return result

    def _process_one(self, imap: imaplib.IMAP4_SSL, uid: bytes, stats: dict) -> bool:
        """단일 메시지 처리. 처리 성공(=archive 가능) 시 True."""
        typ, data = imap.uid("FETCH", uid, "(RFC822)")
        if typ != "OK" or not data or not data[0]:
            return False

        raw = data[0][1] if isinstance(data[0], tuple) else data[0]
        if not isinstance(raw, (bytes, bytearray)):
            return False

        # 원본 메시지에서 Message-ID 추출 (재처리 dedup 키)
        msg = email.message_from_bytes(raw)
        ndr_message_id = (msg.get("Message-ID") or "").strip()[:255]

        failed_email, smtp_code, bounce_type, diagnostic = parse_ndr(bytes(raw))
        if not failed_email:
            logger.info("BounceProcessor: UID %s NDR 파싱 실패 (failed_email 미식별)", uid)
            return False  # archive 하지 않음 (운영자가 수동 검토 가능)

        with get_session() as session:
            entry = BounceLogRepository.create(
                session,
                email=failed_email,
                bounce_type=bounce_type if bounce_type != "unknown" else "soft",
                smtp_code=smtp_code,
                diagnostic=diagnostic,
                ndr_message_id=ndr_message_id or None,
            )
            if entry is None:
                logger.debug("BounceProcessor: UID %s 중복 Message-ID — 스킵", uid)
                return True  # 이미 처리됨 → archive

            stats["processed"] += 1
            if bounce_type == "hard":
                stats["hard"] += 1
                deactivated = SubscriberRepository.deactivate_all_by_email(
                    session, failed_email
                )
                logger.warning(
                    "Hard bounce: email=%s, code=%s, deactivated_subscribers=%d",
                    failed_email, smtp_code, deactivated,
                )
            else:
                stats["soft"] += 1
                logger.info(
                    "Soft bounce: email=%s, code=%s", failed_email, smtp_code
                )
            session.commit()
        return True

    def _archive(self, imap: imaplib.IMAP4_SSL, uid: bytes) -> None:
        """Gmail archive — INBOX 라벨만 제거 (메시지는 All Mail에 남음)"""
        try:
            imap.uid("STORE", uid, "-X-GM-LABELS", r"\\Inbox")
        except Exception as e:
            logger.warning("BounceProcessor: UID %s archive 실패: %s", uid, e)


def run_bounce_processor() -> dict:
    """스케줄러 진입점"""
    return BounceProcessor().process()
