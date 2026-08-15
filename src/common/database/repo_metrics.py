"""수집 메트릭(CollectionMetric) 저장소. P1b 분해(2026-08-15)로 repository.py 에서 분리."""

import logging
from datetime import datetime, date, timedelta
from typing import Optional

from sqlalchemy import and_, func, Integer
from sqlalchemy.orm import Session
from ..timeutil import KST_DATE_MODIFIER as _KST_DATE_MODIFIER, today_start_utc as _today_start_utc
from .models import CollectionMetric

logger = logging.getLogger(__name__)


class CollectionMetricRepository:
    """수집 메트릭 저장소

    collector 가 누적해 둔 _metrics 딕셔너리 리스트를 한 번에 적재한다.
    소비처:
      - 운영 가시화 (admin dashboard)
      - 회귀 감지 (P2 diff 배너)
      - 트랙 E V1 digest 노출 메트릭 집계
    """

    _ALLOWED_KEYS = {
        "data_type", "api_path",
        "raw_count", "final_count",
        "excluded_by_ids", "excluded_by_companies",
        "effective_days", "fallback_used", "latency_ms", "error",
    }

    @staticmethod
    def record_many(
        session: Session,
        tenant_id: str,
        newsletter_type: str,
        metrics: list[dict],
    ) -> int:
        """수집 메트릭 다건 적재. 실제 INSERT 된 건수 반환.

        - `metrics` 각 항목은 collector 의 `_track()` 컨텍스트 매니저가 채운 dict
        - 필수 키: `data_type`. 나머지는 default 적용.
        - 빈 리스트면 0 반환 (no-op).
        """
        if not metrics:
            return 0
        rows: list[CollectionMetric] = []
        now = datetime.utcnow()
        for m in metrics:
            data_type = m.get("data_type")
            if not data_type:
                continue
            row = CollectionMetric(
                tenant_id=tenant_id,
                newsletter_type=newsletter_type or "daily",
                data_type=str(data_type)[:50],
                api_path=(m.get("api_path") or None),
                raw_count=int(m.get("raw_count") or 0),
                final_count=int(m.get("final_count") or 0),
                excluded_by_ids=int(m.get("excluded_by_ids") or 0),
                excluded_by_companies=int(m.get("excluded_by_companies") or 0),
                effective_days=(
                    int(m["effective_days"])
                    if m.get("effective_days") is not None else None
                ),
                fallback_used=bool(m.get("fallback_used") or False),
                latency_ms=int(m.get("latency_ms") or 0),
                error=((m.get("error") or None) and str(m.get("error"))[:500]),
                collected_at=now,
            )
            rows.append(row)
        if not rows:
            return 0
        session.add_all(rows)
        session.flush()
        return len(rows)

    @staticmethod
    def get_recent(
        session: Session,
        tenant_id: Optional[str] = None,
        days: int = 7,
        limit: int = 500,
    ) -> list[CollectionMetric]:
        """최근 N일 수집 메트릭 (admin 패널 raw 뷰)."""
        since = _today_start_utc() - timedelta(days=days)
        query = session.query(CollectionMetric).filter(
            CollectionMetric.collected_at >= since
        )
        if tenant_id:
            query = query.filter(CollectionMetric.tenant_id == tenant_id)
        return (
            query
            .order_by(CollectionMetric.collected_at.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def get_daily_summary(
        session: Session,
        tenant_id: str,
        days: int = 7,
    ) -> list[dict]:
        """일자 × data_type 별 합계/평균.

        Returns: [{date, data_type, n, sum_raw, sum_final,
                   avg_latency_ms, fallback_n, error_n}, ...]
        """
        since = _today_start_utc() - timedelta(days=days)
        rows = (
            session.query(
                func.date(
                    CollectionMetric.collected_at, _KST_DATE_MODIFIER
                ).label("date"),
                CollectionMetric.data_type,
                func.count(CollectionMetric.id).label("n"),
                func.sum(CollectionMetric.raw_count).label("sum_raw"),
                func.sum(CollectionMetric.final_count).label("sum_final"),
                func.avg(CollectionMetric.latency_ms).label("avg_latency_ms"),
                func.sum(
                    func.cast(CollectionMetric.fallback_used, Integer)
                ).label("fallback_n"),
                func.sum(
                    func.cast(
                        CollectionMetric.error.isnot(None), Integer
                    )
                ).label("error_n"),
            )
            .filter(
                and_(
                    CollectionMetric.tenant_id == tenant_id,
                    CollectionMetric.collected_at >= since,
                )
            )
            .group_by(
                func.date(CollectionMetric.collected_at, _KST_DATE_MODIFIER),
                CollectionMetric.data_type,
            )
            .order_by(
                func.date(
                    CollectionMetric.collected_at, _KST_DATE_MODIFIER
                ).desc(),
                CollectionMetric.data_type.asc(),
            )
            .all()
        )
        return [
            {
                "date": str(r.date),
                "data_type": r.data_type,
                "n": int(r.n or 0),
                "sum_raw": int(r.sum_raw or 0),
                "sum_final": int(r.sum_final or 0),
                "avg_latency_ms": int(r.avg_latency_ms or 0),
                "fallback_n": int(r.fallback_n or 0),
                "error_n": int(r.error_n or 0),
            }
            for r in rows
        ]

    @staticmethod
    def purge_older_than(session: Session, days: int = 90) -> int:
        """보존 기간 초과 메트릭 삭제. sent_articles 와 동일 정책."""
        cutoff = _today_start_utc() - timedelta(days=days)
        deleted = (
            session.query(CollectionMetric)
            .filter(CollectionMetric.collected_at < cutoff)
            .delete(synchronize_session=False)
        )
        return int(deleted or 0)
