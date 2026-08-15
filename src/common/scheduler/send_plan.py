"""발송 계획(SendPlan) — run_send_job 의 모드 결정을 한 곳에 모은다.

기존에는 manual / holiday_test / stale_alert / duplicate_alert 불리언 4개가
run_send_job 전체의 가드 6곳에 조합으로 반복 등장했다. SendPlan 은 초입에서
한 번(경보 발동 시 한 번 더) 계산되고, 이후 코드는 plan 필드만 읽는다.
새 발송 모드(예: 휴일 다음 영업일 catch-up)는 생성자 함수 추가로 끝난다.

축 정의는 SEND_TYPE_SEPARATION.md 표준을 따른다:
  - history_type → send_history.newsletter_type (어떤 콘텐츠인가)
  - send_mode    → send_history.send_mode (누구에게·통계 포함 여부)
"""

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class SendPlan:
    """한 번의 run_send_job 실행이 따를 발송 정책."""

    mode: str
    """normal | manual | weekend_test | holiday_test
    | stale_admin_alert | duplicate_content_alert"""

    history_type: str      # send_history.newsletter_type 로 기록될 값
    send_mode: str         # send_history.send_mode 로 기록될 값
    admin_only: bool       # True 면 SUPER_ADMIN_EMAILS 에게만 발송
    dedup: bool            # 구독자 단위 중복 방지(오늘/기간 내 재발송 차단) 적용
    archive: bool          # newsletter_archives 기록 여부
    record_articles: bool  # sent_articles(기사 dedup 풀) 기록 여부


def normal_plan(newsletter_type: str) -> SendPlan:
    """자동 스케줄 정식 발송."""
    return SendPlan(
        mode="normal", history_type=newsletter_type, send_mode="normal",
        admin_only=False, dedup=True, archive=True, record_articles=True,
    )


def manual_plan(newsletter_type: str) -> SendPlan:
    """관리자 수동 발송 — dedup·아카이브·기사 기록 없음 (긴급 재발송/테스트).

    newsletter_type 축을 'manual' 로 격리해 자동 발송 이력에 영향을 주지
    않는다 (2026-03-21 dedup 오염 사고의 재발 방지 축).
    """
    return SendPlan(
        mode="manual", history_type="manual", send_mode="normal",
        admin_only=False, dedup=False, archive=False, record_articles=False,
    )


def holiday_test_plan(newsletter_type: str, reason: str) -> SendPlan:
    """휴일 관리자 테스트 발송 (HOLIDAY_ADMIN_TEST_ENABLED=true 일 때만 사용).

    reason: 'weekend' | 'holiday' — 통계 축 분리를 위해 send_mode 가 갈린다.
    """
    send_mode = "weekend_test" if reason == "weekend" else "holiday_test"
    return SendPlan(
        mode=send_mode, history_type=newsletter_type, send_mode=send_mode,
        admin_only=True, dedup=False, archive=False, record_articles=False,
    )


def alert_plan(base: SendPlan, kind: str) -> SendPlan:
    """stale/duplicate 경보 — 일반 구독자 발송 중단, 관리자에게만 배너 발송.

    kind: 'stale_admin_alert' | 'duplicate_content_alert'
    dedup 풀·아카이브를 오염시키지 않도록 기록을 전부 격리한다 (ADR-004).
    """
    return replace(
        base, mode=kind, send_mode=kind,
        admin_only=True, dedup=False, archive=False, record_articles=False,
    )
