"""발송 기사 이력(SentArticle) 저장소 — 기사 단위 dedup 풀. P1b 분해(2026-08-15)로 repository.py 에서 분리."""

import logging
from datetime import date, timedelta

from sqlalchemy import and_
from sqlalchemy.orm import Session
from ..timeutil import today_start_utc as _today_start_utc
from .models import SentArticle

logger = logging.getLogger(__name__)


def _insert_ignore(session: Session, payload: list[dict]):
    """UNIQUE 충돌 무시 INSERT — 방언별 분기 (PG 마이그레이션 준비, P2).

    기존에는 sqlite 방언 하드코딩이라 PostgreSQL 전환의 차단 지점이었다.
    두 방언 모두 on_conflict_do_nothing 을 지원하므로 런타임 분기로 중립화.
    """
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as _insert
    else:
        from sqlalchemy.dialects.sqlite import insert as _insert
    stmt = _insert(SentArticle).values(payload)
    return stmt.on_conflict_do_nothing(
        index_elements=["tenant_id", "article_id", "section", "sent_date"]
    )


class SentArticleRepository:
    """발송 기사 이력 저장소 (교차일 dedup 용)

    동일 테넌트에서 최근 N일 내 이미 발송된 기사 ID 를 조회하여
    수집/선정 단계에서 제외(exclude_ids)하기 위한 저장소.
    `UNIQUE(tenant_id, article_id, section, sent_date)` 로 멱등 보장.
    """

    @staticmethod
    def list_recent_article_ids(session: Session, tenant_id: str,
                                days: int = 7) -> list[int]:
        """최근 N일(KST 기준) 내 해당 테넌트에서 발송된 article_id 목록.

        Returns:
            중복 제거된 article_id 리스트 (sent_at DESC 순).
        """
        cutoff = _today_start_utc() - timedelta(days=days)
        rows = (
            session.query(SentArticle.article_id)
            .filter(
                and_(
                    SentArticle.tenant_id == tenant_id,
                    SentArticle.sent_at >= cutoff,
                )
            )
            .order_by(SentArticle.sent_at.desc())
            .distinct()
            .all()
        )
        return [row[0] for row in rows]

    @staticmethod
    def list_recent_company_names(session: Session, tenant_id: str,
                                   days: int = 7) -> list[str]:
        """최근 N일 내 해당 테넌트에서 발송된 기업명 목록.

        company-digest 일 단위 반복 노출 차단용. 헤드라인/디그스트 양쪽에
        남긴 company_name 을 모두 모으되 None/빈문자열은 제외한다.
        """
        cutoff = _today_start_utc() - timedelta(days=days)
        rows = (
            session.query(SentArticle.company_name)
            .filter(
                and_(
                    SentArticle.tenant_id == tenant_id,
                    SentArticle.sent_at >= cutoff,
                    SentArticle.company_name.isnot(None),
                    SentArticle.company_name != "",
                )
            )
            .order_by(SentArticle.sent_at.desc())
            .distinct()
            .all()
        )
        return [row[0] for row in rows]

    @staticmethod
    def record_sent_articles(
        session: Session, tenant_id: str,
        sent_date: date,
        entries: list[tuple],
    ) -> int:
        """발송된 기사 이력을 기록 (멱등: 중복 키는 무시).

        Args:
            entries: 4-튜플 `(article_id, article_url, section, company_name)` 권장.
                3-튜플 `(article_id, article_url, section)` 도 호환(기업명 None).

        Returns:
            실제로 신규 INSERT 된 건수.
        """
        if not entries:
            return 0

        payload = []
        for entry in entries:
            if len(entry) == 4:
                aid, url, section, company = entry
            elif len(entry) == 3:
                aid, url, section = entry
                company = None
            else:
                continue
            if aid is None or not section:
                continue
            payload.append({
                "tenant_id": tenant_id,
                "article_id": aid,
                # 테넌트 중립 문자열 키 (P2) — string-ID 테넌트 대비
                "article_key": str(aid)[:64],
                "article_url": url,
                "section": section,
                "sent_date": sent_date,
                "company_name": (company or None),
            })
        if not payload:
            return 0
        stmt = _insert_ignore(session, payload)
        result = session.execute(stmt)
        return result.rowcount or 0

    @staticmethod
    def list_recent_article_keys(session: Session, tenant_id: str,
                                 days: int = 7) -> list[str]:
        """최근 N일 내 발송 기사의 테넌트 중립 문자열 키 목록 (P2).

        string-ID 테넌트가 dedup 을 채택할 때 사용. int-ID 테넌트의
        기존 이력은 마이그레이션이 str(article_id) 로 백필한다.
        """
        cutoff = _today_start_utc() - timedelta(days=days)
        rows = (
            session.query(SentArticle.article_key)
            .filter(
                and_(
                    SentArticle.tenant_id == tenant_id,
                    SentArticle.sent_at >= cutoff,
                    SentArticle.article_key.isnot(None),
                )
            )
            .order_by(SentArticle.sent_at.desc())
            .distinct()
            .all()
        )
        return [row[0] for row in rows]

    @staticmethod
    def purge_older_than(session: Session, days: int = 90) -> int:
        """보존 기간(기본 90일) 초과 이력 삭제. 주간 cron 용.

        Returns:
            삭제된 행 수.
        """
        cutoff = _today_start_utc() - timedelta(days=days)
        deleted = (
            session.query(SentArticle)
            .filter(SentArticle.sent_at < cutoff)
            .delete(synchronize_session=False)
        )
        return int(deleted or 0)
