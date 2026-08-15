"""수집 데이터 캐시/이력(CollectedData·History) 저장소. P1b 분해(2026-08-15)로 repository.py 에서 분리."""

import json
import logging
from datetime import datetime, date
from typing import Optional

from sqlalchemy import and_, func
from sqlalchemy.orm import Session
from .models import CollectedData, CollectedDataHistory

logger = logging.getLogger(__name__)


class CollectedDataRepository:
    """수집 데이터 저장소"""

    @staticmethod
    def upsert(session: Session, tenant_id: str, data_type: str, data: dict) -> CollectedData:
        """데이터 저장 (기존 데이터 덮어쓰기)"""
        existing = session.query(CollectedData).filter(
            and_(
                CollectedData.tenant_id == tenant_id,
                CollectedData.data_type == data_type
            )
        ).first()

        data_json = json.dumps(data, ensure_ascii=False, default=str)

        if existing:
            existing.data_json = data_json
            existing.collected_at = datetime.utcnow()
            session.flush()
            return existing

        record = CollectedData(
            tenant_id=tenant_id,
            data_type=data_type,
            data_json=data_json
        )
        session.add(record)
        session.flush()
        return record

    @staticmethod
    def get_latest(session: Session, tenant_id: str, data_type: str) -> Optional[dict]:
        """최신 수집 데이터 조회"""
        record = session.query(CollectedData).filter(
            and_(
                CollectedData.tenant_id == tenant_id,
                CollectedData.data_type == data_type
            )
        ).order_by(CollectedData.collected_at.desc()).first()

        if record:
            return json.loads(record.data_json)
        return None

    @staticmethod
    def get_all_latest(session: Session, tenant_id: str) -> dict:
        """테넌트의 모든 최신 수집 데이터 조회"""
        from sqlalchemy import func

        subquery = (
            session.query(
                CollectedData.data_type,
                func.max(CollectedData.id).label("max_id")
            )
            .filter(CollectedData.tenant_id == tenant_id)
            .group_by(CollectedData.data_type)
            .subquery()
        )

        records = (
            session.query(CollectedData)
            .join(subquery, CollectedData.id == subquery.c.max_id)
            .all()
        )

        result = {}
        for record in records:
            result[record.data_type] = json.loads(record.data_json)
        return result


    @staticmethod
    def get_all_latest_with_time(session: Session, tenant_id: str) -> dict:
        """테넌트의 모든 최신 수집 데이터와 수집 시각 함께 반환

        Returns:
            {data_type: (data_dict, collected_at)}
        """
        from sqlalchemy import func

        subquery = (
            session.query(
                CollectedData.data_type,
                func.max(CollectedData.id).label("max_id")
            )
            .filter(CollectedData.tenant_id == tenant_id)
            .group_by(CollectedData.data_type)
            .subquery()
        )

        records = (
            session.query(CollectedData)
            .join(subquery, CollectedData.id == subquery.c.max_id)
            .all()
        )

        result = {}
        for record in records:
            result[record.data_type] = (
                json.loads(record.data_json),
                record.collected_at,
            )
        return result

    @staticmethod
    def save_to_history(session: Session, tenant_id: str, data_type: str,
                        data: dict, collected_date: date = None) -> CollectedDataHistory:
        """일일 수집 데이터를 이력 테이블에 저장 (upsert)"""
        if collected_date is None:
            collected_date = date.today()

        data_json = json.dumps(data, ensure_ascii=False, default=str)

        existing = session.query(CollectedDataHistory).filter(
            and_(
                CollectedDataHistory.tenant_id == tenant_id,
                CollectedDataHistory.data_type == data_type,
                CollectedDataHistory.collected_date == collected_date,
            )
        ).first()

        if existing:
            existing.data_json = data_json
            existing.collected_at = datetime.utcnow()
            session.flush()
            return existing

        record = CollectedDataHistory(
            tenant_id=tenant_id,
            data_type=data_type,
            data_json=data_json,
            collected_date=collected_date,
        )
        session.add(record)
        session.flush()
        return record

    @staticmethod
    def get_history_range(session: Session, tenant_id: str,
                          date_from: date, date_to: date) -> list[dict]:
        """기간별 이력 조회 - 날짜별 수집 데이터 리스트 반환

        Returns:
            [{collected_date, data_type, data}, ...]
        """
        records = (
            session.query(CollectedDataHistory)
            .filter(
                and_(
                    CollectedDataHistory.tenant_id == tenant_id,
                    CollectedDataHistory.collected_date >= date_from,
                    CollectedDataHistory.collected_date <= date_to,
                )
            )
            .order_by(CollectedDataHistory.collected_date.asc())
            .all()
        )
        return [
            {
                "collected_date": record.collected_date,
                "data_type": record.data_type,
                "data": json.loads(record.data_json),
            }
            for record in records
        ]
