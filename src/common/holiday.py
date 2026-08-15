"""휴일(비영업일) 판정 모듈.

주말/공휴일 발송 정책(HOLIDAY_SEND_POLICY)의 판정 축:
  - 주말: 토(5)/일(6)
  - 법정 공휴일: holidays 패키지 KR 달력 (대체휴일 포함)
  - 추가 휴일: settings.extra_holidays (YYYY-MM-DD 콤마 구분, 회사 지정 휴무일)

판정 결과는 스케줄러가 휴일 자동 발송 스킵(기본) 또는 관리자 테스트 발송 모드
(HOLIDAY_ADMIN_TEST_ENABLED=true, send_mode='weekend_test'/'holiday_test')
분기에 사용한다. 발송 파이프라인을 막지 않도록 fail-safe:
holidays 패키지 미설치·오류 시 주말 판정만 수행하고 경고 로그를 남긴다.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

from ..config import settings

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

try:
    import holidays as _holidays_lib
except ImportError:  # pragma: no cover - requirements 에 포함되므로 방어용
    _holidays_lib = None
    logger.warning("holidays 패키지 미설치 — 공휴일 판정 없이 주말만 적용")


@lru_cache(maxsize=8)
def _kr_holidays_for_year(year: int):
    """해당 연도 KR 공휴일 셋. 라이브러리 오류 시 None (주말만 판정)."""
    if _holidays_lib is None:
        return None
    try:
        return _holidays_lib.country_holidays("KR", years=[year])
    except Exception as e:
        logger.warning(f"KR 공휴일 달력 로드 실패 ({year}년) — 주말만 적용: {e}")
        return None


@lru_cache(maxsize=1)
def _extra_holidays_cached(raw: str) -> frozenset:
    """extra_holidays 설정 파싱. 잘못된 항목은 경고 후 무시.

    raw 를 인자로 받아 캐시 — 설정이 바뀌면(테스트 등) 키가 달라져 재파싱된다.
    """
    result = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            result.add(date.fromisoformat(token))
        except ValueError:
            logger.warning(f"EXTRA_HOLIDAYS 형식 오류 무시 (YYYY-MM-DD 필요): {token!r}")
    return frozenset(result)


def non_business_day_reason(d: Optional[date] = None) -> Optional[str]:
    """비영업일 사유 반환. 영업일이면 None.

    Returns:
        'weekend'  — 토/일
        'holiday'  — 법정 공휴일(대체휴일 포함) 또는 extra_holidays 지정일
        None       — 평일 영업일
    주말과 공휴일이 겹치면(예: 토요일 광복절) 'weekend' 우선 — 기존
    weekend_test 통계 축 유지.
    """
    if d is None:
        d = datetime.now(KST).date()

    if d.weekday() >= 5:
        return "weekend"

    kr = _kr_holidays_for_year(d.year)
    if kr is not None and d in kr:
        return "holiday"

    if d in _extra_holidays_cached(settings.extra_holidays):
        return "holiday"

    return None


def is_business_day(d: Optional[date] = None) -> bool:
    """영업일(평일이며 공휴일 아님) 여부."""
    return non_business_day_reason(d) is None


def holiday_name(d: date) -> Optional[str]:
    """공휴일명 (로그/배너 표기용). 공휴일이 아니면 None."""
    kr = _kr_holidays_for_year(d.year)
    if kr is not None and d in kr:
        return kr.get(d)
    if d in _extra_holidays_cached(settings.extra_holidays):
        return "지정 휴무일"
    return None
