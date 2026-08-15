"""시간대 유틸 — KST 정의와 날짜 경계 계산의 단일 출처.

DB 는 naive UTC(datetime.utcnow)로 저장하고, 사용자 대면 "하루" 경계는
KST 자정이다. 발송 시간대(06:40~09:30 KST)가 UTC 로는 전날 21:40~00:30 이라,
UTC 그대로 날짜를 자르면 집계·dedup 이 하루씩 어긋난다.
이 모듈 외의 곳에서 KST 를 재정의하지 말 것.
"""

from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

# SQLite date() 의 KST 보정 modifier — func.date(col, KST_DATE_MODIFIER).
# (PostgreSQL 이관 시 AT TIME ZONE 표현으로 교체 필요)
KST_DATE_MODIFIER = "+9 hours"


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> date:
    return datetime.now(KST).date()


def today_start_utc() -> datetime:
    """KST 기준 오늘 자정을 naive UTC datetime 으로 반환.

    컨테이너 TZ=Asia/Seoul 환경에서 datetime.utcnow()를 경계로 쓰면
    UTC 자정(=KST 09:00)이 경계가 되어 전일 09:00~당일 08:59 KST 가
    같은 "오늘"로 묶인다. KST 자정을 UTC 로 환산(전일 15:00 UTC)해 사용한다.
    """
    midnight_kst = datetime.now(KST).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return midnight_kst.astimezone(timezone.utc).replace(tzinfo=None)
