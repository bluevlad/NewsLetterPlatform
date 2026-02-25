"""
구독자 마이그레이션 스크립트
academy-insight → teacher-hub 테넌트 통합

사용법:
  python scripts/migrate_subscribers.py --dry-run   # 예상 결과만 출력
  python scripts/migrate_subscribers.py              # 실제 마이그레이션 실행
"""

import argparse
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text
from src.config import settings
from src.common.database import init_db, get_session

SOURCE_TENANT = "academy-insight"
TARGET_TENANT = "teacher-hub"


def get_counts(session):
    """현재 구독자/이력 수 조회"""
    source_active = session.execute(
        text("SELECT COUNT(*) FROM subscribers WHERE tenant_id = :tid AND is_active = 1"),
        {"tid": SOURCE_TENANT},
    ).scalar()

    source_inactive = session.execute(
        text("SELECT COUNT(*) FROM subscribers WHERE tenant_id = :tid AND is_active = 0"),
        {"tid": SOURCE_TENANT},
    ).scalar()

    target_active = session.execute(
        text("SELECT COUNT(*) FROM subscribers WHERE tenant_id = :tid AND is_active = 1"),
        {"tid": TARGET_TENANT},
    ).scalar()

    send_history_count = session.execute(
        text("SELECT COUNT(*) FROM send_history WHERE tenant_id = :tid"),
        {"tid": SOURCE_TENANT},
    ).scalar()

    collected_data_count = session.execute(
        text("SELECT COUNT(*) FROM collected_data WHERE tenant_id = :tid"),
        {"tid": SOURCE_TENANT},
    ).scalar()

    return {
        "source_active": source_active,
        "source_inactive": source_inactive,
        "target_active": target_active,
        "send_history": send_history_count,
        "collected_data": collected_data_count,
    }


def find_duplicates(session):
    """teacher-hub에 이미 존재하는 이메일 목록"""
    rows = session.execute(
        text("""
            SELECT s1.email
            FROM subscribers s1
            JOIN subscribers s2 ON s1.email = s2.email
            WHERE s1.tenant_id = :source AND s2.tenant_id = :target
              AND s1.is_active = 1 AND s2.is_active = 1
        """),
        {"source": SOURCE_TENANT, "target": TARGET_TENANT},
    ).fetchall()
    return [row[0] for row in rows]


def migrate(session, dry_run: bool):
    """구독자 마이그레이션 실행"""
    counts = get_counts(session)
    duplicates = find_duplicates(session)

    print("=" * 60)
    print(f"  구독자 마이그레이션: {SOURCE_TENANT} → {TARGET_TENANT}")
    print("=" * 60)
    print(f"\n  [현재 상태]")
    print(f"  {SOURCE_TENANT} 활성 구독자: {counts['source_active']}명")
    print(f"  {SOURCE_TENANT} 비활성 구독자: {counts['source_inactive']}명")
    print(f"  {TARGET_TENANT} 활성 구독자: {counts['target_active']}명")
    print(f"  발송 이력: {counts['send_history']}건")
    print(f"  수집 데이터: {counts['collected_data']}건")
    print(f"\n  중복 이메일 (건너뜀): {len(duplicates)}건")
    if duplicates:
        for email in duplicates[:5]:
            print(f"    - {email}")
        if len(duplicates) > 5:
            print(f"    ... 외 {len(duplicates) - 5}건")

    migrate_count = counts["source_active"] - len(duplicates)
    print(f"\n  이전 대상: {migrate_count}명")

    if dry_run:
        print(f"\n  [DRY-RUN] 실제 변경 없음")
        print("=" * 60)
        return

    print(f"\n  [실행 중...]")

    # 1. 중복 이메일은 academy-insight 쪽 비활성화
    if duplicates:
        for email in duplicates:
            session.execute(
                text("""
                    UPDATE subscribers SET is_active = 0
                    WHERE tenant_id = :source AND email = :email AND is_active = 1
                """),
                {"source": SOURCE_TENANT, "email": email},
            )
        print(f"  - 중복 구독자 비활성화: {len(duplicates)}건")

    # 2. 나머지 활성 구독자 tenant_id 변경
    result = session.execute(
        text("""
            UPDATE subscribers SET tenant_id = :target
            WHERE tenant_id = :source AND is_active = 1
        """),
        {"source": SOURCE_TENANT, "target": TARGET_TENANT},
    )
    print(f"  - 구독자 이전: {result.rowcount}명")

    # 3. send_history tenant_id 변경
    result = session.execute(
        text("UPDATE send_history SET tenant_id = :target WHERE tenant_id = :source"),
        {"source": SOURCE_TENANT, "target": TARGET_TENANT},
    )
    print(f"  - 발송 이력 이전: {result.rowcount}건")

    # 4. collected_data tenant_id 변경
    result = session.execute(
        text("UPDATE collected_data SET tenant_id = :target WHERE tenant_id = :source"),
        {"source": SOURCE_TENANT, "target": TARGET_TENANT},
    )
    print(f"  - 수집 데이터 이전: {result.rowcount}건")

    # 결과 확인
    new_counts = get_counts(session)
    print(f"\n  [마이그레이션 완료]")
    print(f"  {TARGET_TENANT} 활성 구독자: {new_counts['target_active']}명")
    print(f"  {SOURCE_TENANT} 잔여 활성: {new_counts['source_active']}명")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="academy-insight → teacher-hub 구독자 마이그레이션"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실제 변경 없이 예상 결과만 출력",
    )
    args = parser.parse_args()

    init_db(settings.database_url)

    with get_session() as session:
        migrate(session, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
