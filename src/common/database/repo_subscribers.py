"""구독자(Subscriber) 저장소. P1b 분해(2026-08-15)로 repository.py 에서 분리."""

import json
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session
from .models import Subscriber

logger = logging.getLogger(__name__)


class SubscriberRepository:
    """구독자 저장소"""

    @staticmethod
    def create(session: Session, tenant_id: str, email: str, name: str,
               unsubscribe_token: str,
               persona_code: Optional[str] = None,
               purpose: Optional[str] = None,
               depth_level: str = "practical",
               interests: Optional[list] = None) -> Subscriber:
        """구독자 생성. 페르소나 인자는 선택 — 미지정 시 기존과 동일 동작."""
        subscriber = Subscriber(
            tenant_id=tenant_id,
            email=email,
            name=name,
            unsubscribe_token=unsubscribe_token,
            persona_code=persona_code or None,
            purpose=purpose or None,
            depth_level=depth_level or "practical",
            interests=(json.dumps(interests, ensure_ascii=False)
                       if interests else None),
        )
        session.add(subscriber)
        session.flush()
        return subscriber

    @staticmethod
    def get_by_email(session: Session, tenant_id: str, email: str) -> Optional[Subscriber]:
        return session.query(Subscriber).filter(
            and_(Subscriber.tenant_id == tenant_id, Subscriber.email == email)
        ).first()

    @staticmethod
    def get_active_by_email(session: Session, tenant_id: str, email: str) -> Optional[Subscriber]:
        return session.query(Subscriber).filter(
            and_(
                Subscriber.tenant_id == tenant_id,
                Subscriber.email == email,
                Subscriber.is_active == True
            )
        ).first()

    @staticmethod
    def get_all_active(session: Session, tenant_id: str) -> list[Subscriber]:
        return session.query(Subscriber).filter(
            and_(Subscriber.tenant_id == tenant_id, Subscriber.is_active == True)
        ).all()

    @staticmethod
    def get_active_by_slot(session: Session, tenant_id: str, slot: str) -> list[Subscriber]:
        """특정 슬롯에 속한 활성 구독자 (NULL 슬롯은 DEFAULT_SLOT으로 간주)"""
        from ..scheduler.slots import DEFAULT_SLOT
        if slot == DEFAULT_SLOT:
            slot_filter = or_(Subscriber.send_slot == slot, Subscriber.send_slot.is_(None))
        else:
            slot_filter = Subscriber.send_slot == slot
        return session.query(Subscriber).filter(
            and_(
                Subscriber.tenant_id == tenant_id,
                Subscriber.is_active == True,
                slot_filter,
            )
        ).all()

    @staticmethod
    def count_by_slot(session: Session, tenant_id: str) -> dict:
        """슬롯별 활성 구독자 수: {'early': N, 'mid': N, 'late': N}"""
        from ..scheduler.slots import DEFAULT_SLOT, SLOT_KEYS
        rows = (
            session.query(Subscriber.send_slot, func.count(Subscriber.id))
            .filter(
                and_(
                    Subscriber.tenant_id == tenant_id,
                    Subscriber.is_active == True,
                )
            )
            .group_by(Subscriber.send_slot)
            .all()
        )
        result = {key: 0 for key in SLOT_KEYS}
        for slot, cnt in rows:
            key = slot if slot in SLOT_KEYS else DEFAULT_SLOT
            result[key] = result.get(key, 0) + cnt
        return result

    @staticmethod
    def bulk_update_slot(session: Session, tenant_id: str,
                         subscriber_ids: list[int], new_slot: str) -> int:
        """선택한 구독자들의 슬롯을 일괄 변경. 변경된 행 수 반환"""
        if not subscriber_ids:
            return 0
        updated = (
            session.query(Subscriber)
            .filter(
                and_(
                    Subscriber.tenant_id == tenant_id,
                    Subscriber.id.in_(subscriber_ids),
                )
            )
            .update({Subscriber.send_slot: new_slot}, synchronize_session=False)
        )
        session.flush()
        return updated

    @staticmethod
    def update_slot(session: Session, subscriber_id: int, new_slot: str) -> bool:
        subscriber = session.query(Subscriber).filter(Subscriber.id == subscriber_id).first()
        if not subscriber:
            return False
        subscriber.send_slot = new_slot
        session.flush()
        return True

    @staticmethod
    def delete(session: Session, subscriber_id: int) -> bool:
        """구독자 영구 삭제. 삭제되면 True, 없으면 False"""
        subscriber = session.query(Subscriber).filter(Subscriber.id == subscriber_id).first()
        if not subscriber:
            return False
        session.delete(subscriber)
        session.flush()
        return True

    @staticmethod
    def get_by_unsubscribe_token(session: Session, token: str) -> Optional[Subscriber]:
        return session.query(Subscriber).filter(
            and_(Subscriber.unsubscribe_token == token, Subscriber.is_active == True)
        ).first()

    @staticmethod
    def deactivate_all_by_email(session: Session, email: str) -> int:
        """이메일이 hard bounce된 경우 전 테넌트의 동일 이메일 구독자 비활성화. 변경 행 수 반환"""
        updated = (
            session.query(Subscriber)
            .filter(and_(Subscriber.email == email, Subscriber.is_active == True))
            .update(
                {Subscriber.is_active: False, Subscriber.updated_at: datetime.utcnow()},
                synchronize_session=False,
            )
        )
        session.flush()
        return updated

    @staticmethod
    def count_by_tenant(session: Session, tenant_id: str, active_only: bool = True) -> int:
        """테넌트별 구독자 수"""
        query = session.query(func.count(Subscriber.id)).filter(
            Subscriber.tenant_id == tenant_id
        )
        if active_only:
            query = query.filter(Subscriber.is_active == True)
        return query.scalar() or 0

    @staticmethod
    def get_by_id(session: Session, subscriber_id: int) -> Optional[Subscriber]:
        """ID로 구독자 조회"""
        return session.query(Subscriber).filter(Subscriber.id == subscriber_id).first()

    @staticmethod
    def get_all_by_tenant(
        session: Session,
        tenant_id: str,
        active_only: Optional[bool] = None,
        search: str = "",
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Subscriber], int]:
        """테넌트별 구독자 목록 (페이지네이션, 검색)"""
        query = session.query(Subscriber).filter(Subscriber.tenant_id == tenant_id)
        if active_only is True:
            query = query.filter(Subscriber.is_active == True)
        elif active_only is False:
            query = query.filter(Subscriber.is_active == False)
        if search:
            pattern = f"%{search}%"
            query = query.filter(
                or_(Subscriber.email.ilike(pattern), Subscriber.name.ilike(pattern))
            )
        total = query.count()
        items = query.order_by(Subscriber.created_at.desc()).offset(offset).limit(limit).all()
        return items, total

    # --- 페르소나 적응형 뉴스레터 (N1) ---

    @staticmethod
    def update_persona(session: Session, subscriber_id: int, *,
                       persona_code: Optional[str] = None,
                       purpose: Optional[str] = None,
                       depth_level: Optional[str] = None,
                       interests: Optional[list] = None) -> bool:
        """구독 관리 페이지에서 페르소나 설정 변경.

        None 인자는 '변경 안 함'. 빈 문자열/빈 리스트는 'NULL 로 비움'.
        """
        subscriber = session.query(Subscriber).filter(
            Subscriber.id == subscriber_id
        ).first()
        if not subscriber:
            return False
        if persona_code is not None:
            subscriber.persona_code = persona_code or None
        if purpose is not None:
            subscriber.purpose = purpose or None
        if depth_level is not None:
            subscriber.depth_level = depth_level or "practical"
        if interests is not None:
            subscriber.interests = (
                json.dumps(interests, ensure_ascii=False) if interests else None
            )
        session.flush()
        return True

    @staticmethod
    def get_active_personas(session: Session, tenant_id: str) -> list[str]:
        """활성 구독자에 존재하는 distinct persona_code. NULL 은 'patient' 로 합산.

        N3 페르소나 세그먼트 순회용 선반영.
        """
        rows = (
            session.query(Subscriber.persona_code)
            .filter(and_(
                Subscriber.tenant_id == tenant_id,
                Subscriber.is_active == True,
            ))
            .distinct()
            .all()
        )
        codes = {(code or "patient") for (code,) in rows}
        return sorted(codes)

    @staticmethod
    def get_active_by_persona(session: Session, tenant_id: str,
                              persona_code: str) -> list[Subscriber]:
        """persona_code 세그먼트의 활성 구독자.

        persona_code 가 'patient' 면 persona_code IS NULL 인 행도 합류 (N3 선반영).
        """
        if persona_code == "patient":
            persona_filter = or_(
                Subscriber.persona_code == "patient",
                Subscriber.persona_code.is_(None),
            )
        else:
            persona_filter = Subscriber.persona_code == persona_code
        return session.query(Subscriber).filter(
            and_(
                Subscriber.tenant_id == tenant_id,
                Subscriber.is_active == True,
                persona_filter,
            )
        ).all()
