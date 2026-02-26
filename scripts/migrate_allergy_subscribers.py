"""
AllergyNewsLetter → NewsLetterPlatform 구독자 마이그레이션 스크립트

AllergyNewsLetter DB의 recipients 테이블에서 활성 구독자를 읽어
NewsLetterPlatform DB의 subscribers 테이블에 tenant_id="allergy-insight"로 삽입합니다.

사용법:
  python scripts/migrate_allergy_subscribers.py --dry-run   # 예상 결과만 출력
  python scripts/migrate_allergy_subscribers.py              # 실제 마이그레이션 실행

환경변수:
  ALLERGY_DB_PATH: AllergyNewsLetter DB 경로 (기본: ../AllergyNewsLetter/data/allergynewsletter.db)
"""

import argparse
import hashlib
import secrets
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import create_engine, text
from src.config import settings
from src.common.database import init_db, get_session

TENANT_ID = "allergy-insight"

# AllergyNewsLetter DB 기본 경로
DEFAULT_ALLERGY_DB = Path(__file__).parent.parent.parent / "AllergyNewsLetter" / "data" / "allergynewsletter.db"


def generate_unsubscribe_token(email: str) -> str:
    """구독 해지 토큰 생성"""
    data = f"{email}{secrets.token_hex(16)}{datetime.now().isoformat()}"
    return hashlib.sha256(data.encode()).hexdigest()[:32]


def get_allergy_subscribers(allergy_db_path: str) -> list:
    """AllergyNewsLetter DB에서 활성 구독자 조회"""
    engine = create_engine(f"sqlite:///{allergy_db_path}", echo=False)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT email, name FROM recipients WHERE is_active = 1")
        ).fetchall()
    engine.dispose()
    return [(row[0], row[1]) for row in rows]


def get_existing_emails(session) -> set:
    """Platform DB에서 이미 존재하는 allergy-insight 구독자 이메일"""
    rows = session.execute(
        text("SELECT email FROM subscribers WHERE tenant_id = :tid"),
        {"tid": TENANT_ID},
    ).fetchall()
    return {row[0] for row in rows}


def migrate(session, allergy_db_path: str, dry_run: bool):
    """구독자 마이그레이션 실행"""
    subscribers = get_allergy_subscribers(allergy_db_path)
    existing_emails = get_existing_emails(session)

    new_subscribers = [
        (email, name) for email, name in subscribers
        if email not in existing_emails
    ]
    skipped = len(subscribers) - len(new_subscribers)

    print("=" * 60)
    print(f"  구독자 마이그레이션: AllergyNewsLetter → Platform")
    print(f"  테넌트: {TENANT_ID}")
    print("=" * 60)
    print(f"\n  [현재 상태]")
    print(f"  AllergyNewsLetter 활성 구독자: {len(subscribers)}명")
    print(f"  Platform 기존 구독자: {len(existing_emails)}명")
    print(f"  중복 (건너뜀): {skipped}명")
    print(f"  신규 이전 대상: {len(new_subscribers)}명")

    if dry_run:
        print(f"\n  [DRY-RUN] 실제 변경 없음")
        if new_subscribers:
            print(f"\n  이전 대상 목록:")
            for email, name in new_subscribers[:10]:
                print(f"    - {email} ({name or '이름 없음'})")
            if len(new_subscribers) > 10:
                print(f"    ... 외 {len(new_subscribers) - 10}명")
        print("=" * 60)
        return

    print(f"\n  [실행 중...]")

    inserted = 0
    for email, name in new_subscribers:
        token = generate_unsubscribe_token(email)
        session.execute(
            text("""
                INSERT INTO subscribers (tenant_id, email, name, unsubscribe_token, is_active, created_at, updated_at)
                VALUES (:tid, :email, :name, :token, 1, :now, :now)
            """),
            {
                "tid": TENANT_ID,
                "email": email,
                "name": name or "",
                "token": token,
                "now": datetime.utcnow().isoformat(),
            },
        )
        inserted += 1

    print(f"  - 신규 구독자 삽입: {inserted}명")

    # 결과 확인
    final_count = session.execute(
        text("SELECT COUNT(*) FROM subscribers WHERE tenant_id = :tid AND is_active = 1"),
        {"tid": TENANT_ID},
    ).scalar()

    print(f"\n  [마이그레이션 완료]")
    print(f"  {TENANT_ID} 전체 활성 구독자: {final_count}명")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="AllergyNewsLetter → NewsLetterPlatform 구독자 마이그레이션"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실제 변경 없이 예상 결과만 출력",
    )
    parser.add_argument(
        "--allergy-db",
        type=str,
        default=None,
        help=f"AllergyNewsLetter DB 경로 (기본: {DEFAULT_ALLERGY_DB})",
    )
    args = parser.parse_args()

    allergy_db_path = args.allergy_db or str(DEFAULT_ALLERGY_DB)

    if not Path(allergy_db_path).exists():
        print(f"오류: AllergyNewsLetter DB를 찾을 수 없습니다: {allergy_db_path}")
        sys.exit(1)

    init_db(settings.database_url)

    with get_session() as session:
        migrate(session, allergy_db_path, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
