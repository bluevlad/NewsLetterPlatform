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
