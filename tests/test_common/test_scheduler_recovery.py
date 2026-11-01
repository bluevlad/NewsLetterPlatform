"""스케줄러 장애 복구 회귀 테스트 (#4).

- 잡 공통 옵션: misfire_grace_time / coalesce / max_instances
- 수집 잡 재시도: 실패 시 DateTrigger 재시도 예약, 마지막 시도에서만 경보
"""

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from src.common.scheduler import jobs


@pytest.fixture
def scheduler(monkeypatch):
    # 잡 기본값은 스케줄러 기동 후 add 시점에 적용되므로 paused 로 기동
    sch = BackgroundScheduler(job_defaults=jobs.SCHEDULER_JOB_DEFAULTS)
    sch.start(paused=True)
    monkeypatch.setattr(jobs, "_scheduler", sch)
    yield sch
    sch.shutdown(wait=False)


class TestJobDefaults:
    def test_defaults_applied(self, scheduler):
        job = scheduler.add_job(lambda: None, "interval", minutes=1, id="x")
        assert job.misfire_grace_time == 3600
        assert job.coalesce is True
        assert job.max_instances == 1

    def test_collect_jobs_use_retry_wrapper(self):
        from src.main import register_tenants

        register_tenants()
        sch = BackgroundScheduler()
        jobs.register_all_jobs(sch)
        collect = [j for j in sch.get_jobs() if j.id.startswith("collect")]
        assert collect
        assert all(j.func is jobs.run_scheduled_collect for j in collect)


class TestCollectRetry:
    def _stub(self, monkeypatch, result):
        calls = []

        def fake(tenant_id, newsletter_type="daily", alert_on_failure=True):
            calls.append(alert_on_failure)
            return result

        monkeypatch.setattr(jobs, "run_collect_job", fake)
        return calls

    def test_failure_schedules_retry_without_alert(self, scheduler, monkeypatch):
        calls = self._stub(monkeypatch, False)
        jobs.run_scheduled_collect("t1", "daily")

        assert calls == [False]  # 첫 시도는 경보 억제
        job = scheduler.get_job("retry_collect_t1")
        assert job is not None
        assert job.args == ("t1", "daily", 2)

    def test_weekly_retry_job_id(self, scheduler, monkeypatch):
        self._stub(monkeypatch, False)
        jobs.run_scheduled_collect("t1", "weekly", attempt=2)
        job = scheduler.get_job("retry_collect_weekly_t1")
        assert job.args == ("t1", "weekly", 3)

    def test_last_attempt_alerts_and_stops(self, scheduler, monkeypatch):
        calls = self._stub(monkeypatch, False)
        last = len(jobs.COLLECT_RETRY_DELAYS_SECONDS) + 1
        jobs.run_scheduled_collect("t1", "daily", attempt=last)

        assert calls == [True]
        assert scheduler.get_job("retry_collect_t1") is None

    def test_success_no_retry(self, scheduler, monkeypatch):
        self._stub(monkeypatch, True)
        jobs.run_scheduled_collect("t1", "daily")
        assert scheduler.get_job("retry_collect_t1") is None


# ==================== job_executions (PR-2) ====================

from datetime import timedelta

from apscheduler.triggers.cron import CronTrigger

from src.common.database.repository import (
    init_db, get_session, JobExecutionRepository,
)
from src.common.timeutil import today_kst


@pytest.fixture
def db(tmp_path):
    init_db(f"sqlite:///{tmp_path}/jobexec.db")


class TestJobExecutionRepository:
    def test_attempts_and_success(self, db):
        d = today_kst()
        with get_session() as s:
            JobExecutionRepository.mark_started(s, "t1", "collect", "daily", d)
        with get_session() as s:
            JobExecutionRepository.mark_finished(
                s, "t1", "collect", "daily", d, success=False, error_message="boom"
            )
            assert not JobExecutionRepository.has_succeeded(s, "t1", "collect", "daily", d)
        with get_session() as s:
            row = JobExecutionRepository.mark_started(s, "t1", "collect", "daily", d)
            assert row.attempts == 2 and row.error_message is None
            JobExecutionRepository.mark_finished(s, "t1", "collect", "daily", d, success=True)
        with get_session() as s:
            assert JobExecutionRepository.has_succeeded(s, "t1", "collect", "daily", d)
            assert JobExecutionRepository.has_any(s, "t1")
            assert not JobExecutionRepository.has_any(s, "t2")
            rows = JobExecutionRepository.list_for_date(s, d)
            assert rows[0]["status"] == "success" and rows[0]["attempts"] == 2

    def test_purge(self, db):
        old = today_kst() - timedelta(days=31)
        with get_session() as s:
            JobExecutionRepository.mark_started(s, "t1", "collect", "daily", old)
            JobExecutionRepository.mark_started(s, "t1", "collect", "daily", today_kst())
        with get_session() as s:
            assert JobExecutionRepository.purge_older_than(s, days=30) == 1


class TestIdempotencyGuard:
    def test_send_runs_once_per_day(self, db, monkeypatch):
        calls = []
        monkeypatch.setattr(jobs, "run_send_job", lambda *a, **k: calls.append(k))
        jobs.run_scheduled_send("t1", "daily", slot="early")
        jobs.run_scheduled_send("t1", "daily", slot="early")  # 재기동·중복 트리거
        jobs.run_scheduled_send("t1", "daily", slot="mid")    # 다른 슬롯은 별개
        assert [c["slot"] for c in calls] == ["early", "mid"]
        assert all(c["manual"] is False for c in calls)

    def test_send_failure_not_guarded(self, db, monkeypatch):
        state = {"n": 0}

        def flaky(*a, **k):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("smtp down")

        monkeypatch.setattr(jobs, "run_send_job", flaky)
        jobs.run_scheduled_send("t1", "daily", slot="early")
        with get_session() as s:
            rows = JobExecutionRepository.list_for_date(s, today_kst())
            assert rows[0]["status"] == "failed" and "smtp down" in rows[0]["error"]
        jobs.run_scheduled_send("t1", "daily", slot="early")  # 실패 건은 재실행 허용
        assert state["n"] == 2

    def test_collect_skipped_after_success(self, db, monkeypatch):
        calls = []
        monkeypatch.setattr(
            jobs, "run_collect_job",
            lambda *a, **k: calls.append(1) or True,
        )
        jobs.run_scheduled_collect("t1", "daily")
        jobs.run_scheduled_collect("t1", "daily")
        assert len(calls) == 1


class TestStartupCatchup:
    @pytest.fixture
    def setup(self, db, monkeypatch):
        from datetime import datetime

        sch = BackgroundScheduler()
        now = datetime.now(sch.timezone)
        if now.hour == 0 and now.minute < 30:
            pytest.skip("자정 직후에는 '오늘 지난 시각' 잡을 만들 수 없음")
        monkeypatch.setattr(jobs, "send_ops_alert", lambda *a, **k: None)

        c = now - timedelta(minutes=20)
        snd = now - timedelta(minutes=5)
        sch.add_job(jobs.run_scheduled_collect,
                    CronTrigger(hour=c.hour, minute=c.minute),
                    args=["t1", "daily"], id="collect_t1")
        sch.add_job(jobs.run_scheduled_send,
                    CronTrigger(hour=snd.hour, minute=snd.minute),
                    kwargs={"tenant_id": "t1", "newsletter_type": "daily",
                            "slot": "early"},
                    id="send_t1_early")
        return sch

    def _baseline(self):
        with get_session() as s:
            JobExecutionRepository.mark_started(
                s, "t1", "collect", "daily", today_kst() - timedelta(days=1)
            )

    def test_missed_jobs_scheduled_send_after_collect(self, setup):
        self._baseline()
        ids = jobs.schedule_startup_catchup(setup, window_minutes=120)
        assert set(ids) == {"catchup_collect_t1", "catchup_send_t1_early"}
        c = setup.get_job("catchup_collect_t1").trigger.run_date
        s = setup.get_job("catchup_send_t1_early").trigger.run_date
        assert s - c >= timedelta(minutes=15)

    def test_no_baseline_no_catchup(self, setup):
        assert jobs.schedule_startup_catchup(setup, window_minutes=120) == []

    def test_completed_jobs_skipped(self, setup):
        self._baseline()
        with get_session() as s:
            JobExecutionRepository.mark_started(s, "t1", "send:early", "daily", today_kst())
            JobExecutionRepository.mark_finished(
                s, "t1", "send:early", "daily", today_kst(), success=True
            )
        assert jobs.schedule_startup_catchup(setup, window_minutes=120) == [
            "catchup_collect_t1"
        ]

    def test_outside_window_or_disabled(self, setup):
        self._baseline()
        assert jobs.schedule_startup_catchup(setup, window_minutes=1) == []
        assert jobs.schedule_startup_catchup(setup, window_minutes=0) == []


# ==================== 데이터 보존 정책 (PR-3) ====================

from datetime import datetime as _dt

from src.common.database.models import EmailVerification, SendHistory, VerificationType


class TestDataRetention:
    def _seed(self):
        now = _dt.utcnow()
        with get_session() as s:
            for age in (31, 5):  # 인증: 30일 초과 1건 / 이내 1건
                s.add(EmailVerification(
                    tenant_id="t1", email=f"a{age}@x.com", code="123456",
                    verification_type=VerificationType.SUBSCRIBE,
                    created_at=now - timedelta(days=age),
                    expires_at=now - timedelta(days=age) + timedelta(minutes=10),
                ))
            for age in (91, 60):  # 발송 이력: 90일 초과 1건 / 이내 1건
                s.add(SendHistory(
                    tenant_id="t1", subscriber_id=1, subject="s",
                    is_success=True, sent_at=now - timedelta(days=age),
                ))

    def test_purges_expired_rows_only(self, db):
        self._seed()
        result = jobs.run_data_retention_job()
        assert result["email_verifications"] == 1
        assert result["send_history"] == 1
        assert all(n >= 0 for n in result.values())
        with get_session() as s:
            assert s.query(EmailVerification).count() == 1
            assert s.query(SendHistory).count() == 1

    def test_one_table_failure_isolated(self, db, monkeypatch):
        self._seed()
        alerts = []
        monkeypatch.setattr(jobs, "send_ops_alert", lambda *a, **k: alerts.append(a))

        def boom(*a, **k):
            raise RuntimeError("locked")

        monkeypatch.setattr(jobs.SentArticleRepository, "purge_older_than", boom)
        result = jobs.run_data_retention_job()
        assert result["sent_articles"] == -1
        assert result["send_history"] == 1  # 다른 테이블은 계속 정리
        assert alerts and "sent_articles" in alerts[0][1]

    def test_registered_daily(self):
        from src.main import register_tenants

        register_tenants()
        sch = BackgroundScheduler()
        jobs.register_all_jobs(sch)
        job = sch.get_job("data_retention")
        assert job is not None and job.func is jobs.run_data_retention_job
