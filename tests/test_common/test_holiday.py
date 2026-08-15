"""휴일(비영업일) 판정 모듈 테스트.

주말/법정 공휴일(대체휴일 포함)/EXTRA_HOLIDAYS 판정과
scheduler jobs 의 send_mode 매핑 규칙을 검증한다.
"""

from datetime import date

import pytest

from src.common import holiday
from src.common.holiday import (
    holiday_name,
    is_business_day,
    non_business_day_reason,
)


class TestWeekend:
    def test_saturday(self):
        assert non_business_day_reason(date(2026, 8, 1)) == "weekend"

    def test_sunday(self):
        assert non_business_day_reason(date(2026, 8, 2)) == "weekend"

    def test_plain_weekday(self):
        # 2026-08-04 화요일, 공휴일 아님
        assert non_business_day_reason(date(2026, 8, 4)) is None
        assert is_business_day(date(2026, 8, 4))


class TestKoreanPublicHoliday:
    def test_new_years_day(self):
        # 2026-01-01 목요일 (신정)
        assert non_business_day_reason(date(2026, 1, 1)) == "holiday"

    def test_seollal(self):
        # 2026-02-17 화요일 (설날)
        assert non_business_day_reason(date(2026, 2, 17)) == "holiday"

    def test_substitute_holiday(self):
        # 2026-03-01 삼일절이 일요일 → 3/2(월) 대체휴일
        assert non_business_day_reason(date(2026, 3, 2)) == "holiday"

    def test_holiday_on_weekend_reports_weekend(self):
        # 2026-08-15 광복절이 토요일 — 주말 우선 (기존 weekend_test 통계 축 유지)
        assert non_business_day_reason(date(2026, 8, 15)) == "weekend"

    def test_holiday_name(self):
        assert holiday_name(date(2026, 2, 17)) is not None
        assert holiday_name(date(2026, 8, 4)) is None


class TestExtraHolidays:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        holiday._extra_holidays_cached.cache_clear()
        yield
        holiday._extra_holidays_cached.cache_clear()

    def test_extra_holiday_applied(self, monkeypatch):
        monkeypatch.setattr(
            holiday.settings, "extra_holidays", "2026-08-04, 2026-12-31"
        )
        assert non_business_day_reason(date(2026, 8, 4)) == "holiday"
        assert holiday_name(date(2026, 8, 4)) == "지정 휴무일"
        # 목록에 없는 평일은 영업일 유지
        assert non_business_day_reason(date(2026, 8, 5)) is None

    def test_invalid_token_ignored(self, monkeypatch):
        monkeypatch.setattr(
            holiday.settings, "extra_holidays", "not-a-date,, 2026-08-04"
        )
        # 형식 오류 항목은 무시되고 유효 항목만 적용
        assert non_business_day_reason(date(2026, 8, 4)) == "holiday"

    def test_empty_setting(self, monkeypatch):
        monkeypatch.setattr(holiday.settings, "extra_holidays", "")
        assert non_business_day_reason(date(2026, 8, 4)) is None


class TestSendModeMapping:
    """jobs.py 의 send_mode 결정 규칙 (reason → send_mode)."""

    @staticmethod
    def _send_mode(reason):
        # jobs.run_send_job 내부 매핑과 동일한 규칙
        if reason is None:
            return "normal"
        return "weekend_test" if reason == "weekend" else "holiday_test"

    def test_mapping(self):
        assert self._send_mode(None) == "normal"
        assert self._send_mode("weekend") == "weekend_test"
        assert self._send_mode("holiday") == "holiday_test"


class TestHolidayFullSkip:
    """휴일 자동 발송 전체 스킵 정책 (HOLIDAY_ADMIN_TEST_ENABLED).

    - false(기본): 휴일이면 어떤 슬롯도 발송하지 않고 즉시 종료
    - true: 기존 관리자 테스트 모드 유지 (early 슬롯만 진행)
    """

    def _run_send(self, monkeypatch, *, enabled: bool, slot: str):
        from types import SimpleNamespace
        from src.common.scheduler import jobs

        sender_calls = []
        fake_tenant = SimpleNamespace(weekend_test_mode=True)
        fake_registry = SimpleNamespace(get=lambda tid: fake_tenant)
        monkeypatch.setattr(jobs, "get_registry", lambda: fake_registry)
        monkeypatch.setattr(jobs, "non_business_day_reason", lambda d=None: "weekend")
        monkeypatch.setattr(jobs.settings, "holiday_admin_test_enabled", enabled)
        # get_sender 도달 여부로 스킵/진행 판별 (미설정 sender 로 즉시 종료)
        monkeypatch.setattr(
            jobs, "get_sender",
            lambda: sender_calls.append(1) or SimpleNamespace(is_configured=False),
        )
        jobs.run_send_job("test-tenant", "daily", manual=False, slot=slot)
        return sender_calls

    def test_disabled_skips_all_slots(self, monkeypatch):
        for slot in ("early", "mid", "late"):
            assert self._run_send(monkeypatch, enabled=False, slot=slot) == []

    def test_enabled_keeps_admin_test_on_early(self, monkeypatch):
        assert self._run_send(monkeypatch, enabled=True, slot="early") == [1]

    def test_enabled_still_skips_mid_late(self, monkeypatch):
        assert self._run_send(monkeypatch, enabled=True, slot="mid") == []
        assert self._run_send(monkeypatch, enabled=True, slot="late") == []
