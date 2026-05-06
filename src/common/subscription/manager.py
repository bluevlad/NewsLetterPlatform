"""
구독 관리 매니저
이메일 인증 기반 구독/해지 플로우
"""

import random
import string
import hashlib
import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from ...config import settings
from ..database.models import Subscriber, EmailVerification, VerificationType
from ..database.repository import (
    SubscriberRepository, EmailVerificationRepository
)
from ..security import is_role_account, is_bot_name_pattern

logger = logging.getLogger(__name__)


def generate_verification_code() -> str:
    """6자리 인증코드 생성"""
    return "".join(random.choices(string.digits, k=settings.verification_code_length))


def generate_unsubscribe_token(email: str) -> str:
    """구독 해지 토큰 생성 (cryptographic random)"""
    return secrets.token_urlsafe(32)


class SubscriptionManager:
    """구독 관리 매니저"""

    def request_subscribe(
        self, session: Session, tenant_id: str, email: str, name: str
    ) -> Tuple[bool, str, Optional[int]]:
        """구독 신청 - 인증코드 발송 요청

        Returns:
            (success, message, verification_id)
        """
        email = email.strip().lower()
        name = name.strip()

        # 어뷰즈 방어: role-account 메일함 차단
        if is_role_account(email):
            logger.warning("role-account 차단: tenant=%s, email=%s", tenant_id, email)
            return False, "해당 이메일 주소로는 구독할 수 없습니다.", None

        # 어뷰즈 방어: 이름 컬럼이 봇 자동 생성 무작위 패턴
        if is_bot_name_pattern(name):
            logger.warning("봇 이름 패턴 차단: tenant=%s, email=%s, name=%s",
                          tenant_id, email, name)
            return False, "이름을 다시 확인해주세요.", None

        # 어뷰즈 방어: 이메일 기반 rate limit
        now = datetime.utcnow()
        recent_minutes = EmailVerificationRepository.count_recent_by_email(
            session, email, now - timedelta(minutes=settings.subscribe_rate_limit_email_minutes)
        )
        if recent_minutes >= 1:
            logger.warning("이메일 rate limit (분 단위) 초과: email=%s, count=%d",
                          email, recent_minutes)
            return False, (
                f"방금 인증코드를 발송했습니다. "
                f"{settings.subscribe_rate_limit_email_minutes}분 후 다시 시도해주세요."
            ), None
        recent_day = EmailVerificationRepository.count_recent_by_email(
            session, email, now - timedelta(days=1)
        )
        if recent_day >= settings.subscribe_rate_limit_email_per_day:
            logger.warning("이메일 rate limit (일 단위) 초과: email=%s, count=%d",
                          email, recent_day)
            return False, (
                "오늘 인증코드 발송 한도를 초과했습니다. "
                "내일 다시 시도해주세요."
            ), None

        existing = SubscriberRepository.get_active_by_email(session, tenant_id, email)
        if existing:
            return False, "이미 구독 중인 이메일입니다.", None

        code = generate_verification_code()
        expires_at = now + timedelta(minutes=settings.verification_expiry_minutes)

        EmailVerificationRepository.delete_pending(session, tenant_id, email)

        verification = EmailVerificationRepository.create(
            session, tenant_id, email, name, code,
            VerificationType.SUBSCRIBE, expires_at
        )
        session.flush()

        return True, code, verification.id

    def verify_subscribe(
        self, session: Session, verification_id: int, email: str, code: str
    ) -> Tuple[bool, str, Optional[Subscriber]]:
        """구독 인증코드 확인

        Returns:
            (success, message, subscriber)
        """
        code = code.strip()

        verification = EmailVerificationRepository.get_by_id_and_email(
            session, verification_id, email
        )

        if not verification:
            return False, "인증 정보를 찾을 수 없습니다. 다시 신청해주세요.", None

        if datetime.utcnow() > verification.expires_at:
            return False, "인증코드가 만료되었습니다. 다시 신청해주세요.", None

        if verification.attempts >= settings.max_verification_attempts:
            return False, "인증 시도 횟수를 초과했습니다. 다시 신청해주세요.", None

        if verification.code != code:
            verification.attempts += 1
            session.flush()
            remaining = settings.max_verification_attempts - verification.attempts
            return False, f"인증코드가 일치하지 않습니다. (남은 시도: {remaining}회)", None

        verification.is_verified = True
        tenant_id = verification.tenant_id

        unsubscribe_token = generate_unsubscribe_token(email)

        existing = SubscriberRepository.get_by_email(session, tenant_id, email)
        if existing:
            existing.is_active = True
            existing.name = verification.name or existing.name
            existing.unsubscribe_token = unsubscribe_token
            existing.updated_at = datetime.utcnow()
            subscriber = existing
        else:
            subscriber = SubscriberRepository.create(
                session, tenant_id, email, verification.name, unsubscribe_token
            )

        session.flush()
        logger.info(f"구독 완료: tenant={tenant_id}, email={email}")
        return True, "구독이 완료되었습니다.", subscriber

    def request_unsubscribe(
        self, session: Session, tenant_id: str, email: str
    ) -> Tuple[bool, str, Optional[int]]:
        """구독 해지 신청 - 인증코드 발송 요청

        Returns:
            (success, message, verification_id)
        """
        email = email.strip().lower()

        # 어뷰즈 방어: 이메일 기반 rate limit (해지도 같은 발송 벡터)
        now = datetime.utcnow()
        recent_minutes = EmailVerificationRepository.count_recent_by_email(
            session, email, now - timedelta(minutes=settings.subscribe_rate_limit_email_minutes)
        )
        if recent_minutes >= 1:
            return False, (
                f"방금 인증코드를 발송했습니다. "
                f"{settings.subscribe_rate_limit_email_minutes}분 후 다시 시도해주세요."
            ), None

        subscriber = SubscriberRepository.get_active_by_email(session, tenant_id, email)
        if not subscriber:
            return False, "해당 이메일로 구독 중인 내역이 없습니다.", None

        code = generate_verification_code()
        expires_at = now + timedelta(minutes=settings.verification_expiry_minutes)

        EmailVerificationRepository.delete_pending(
            session, tenant_id, email, VerificationType.UNSUBSCRIBE
        )

        verification = EmailVerificationRepository.create(
            session, tenant_id, email, subscriber.name, code,
            VerificationType.UNSUBSCRIBE, expires_at
        )
        session.flush()

        return True, code, verification.id

    def verify_unsubscribe(
        self, session: Session, verification_id: int, email: str, code: str
    ) -> Tuple[bool, str]:
        """구독 해지 인증코드 확인

        Returns:
            (success, message)
        """
        code = code.strip()

        verification = EmailVerificationRepository.get_unsubscribe_by_id_and_email(
            session, verification_id, email
        )

        if not verification:
            return False, "인증 정보를 찾을 수 없습니다. 다시 신청해주세요."

        if datetime.utcnow() > verification.expires_at:
            return False, "인증코드가 만료되었습니다. 다시 신청해주세요."

        if verification.attempts >= settings.max_verification_attempts:
            return False, "인증 시도 횟수를 초과했습니다. 다시 신청해주세요."

        if verification.code != code:
            verification.attempts += 1
            session.flush()
            remaining = settings.max_verification_attempts - verification.attempts
            return False, f"인증코드가 일치하지 않습니다. (남은 시도: {remaining}회)"

        verification.is_verified = True
        tenant_id = verification.tenant_id

        subscriber = SubscriberRepository.get_active_by_email(session, tenant_id, email)
        if subscriber:
            subscriber.is_active = False
            subscriber.updated_at = datetime.utcnow()
            logger.info(f"구독 해지 완료: tenant={tenant_id}, email={email}")

        session.flush()
        return True, "구독이 해지되었습니다."

    def unsubscribe_by_token(
        self, session: Session, token: str
    ) -> Tuple[bool, str, Optional[str]]:
        """토큰 기반 구독 해지 (이메일 링크용)

        Returns:
            (success, message, email)
        """
        subscriber = SubscriberRepository.get_by_unsubscribe_token(session, token)
        if not subscriber:
            return False, "유효하지 않은 링크이거나 이미 해지된 구독입니다.", None

        email = subscriber.email
        subscriber.is_active = False
        subscriber.updated_at = datetime.utcnow()
        session.flush()

        logger.info(f"토큰 기반 구독 해지: email={email}")
        return True, "구독이 해지되었습니다.", email
