"""P1b 구조 개편 회귀 테스트 (2026-08-15).

- repository facade 재노출 — 기존 호출부가 쓰는 14개 이름 전부 유지
- 테넌트 자동 발견(discover_and_register) — 3개 테넌트, 플랫폼 파일 무수정 등록
- 테넌트 설정 이관 — env 변수명 매핑이 이관 전과 동일하게 유지되는지
"""

import importlib

from src.tenant.registry import discover_and_register, get_registry


class TestRepositoryFacade:
    """분해 후에도 repository 모듈이 기존 공개 이름을 전부 재노출한다."""

    LEGACY_NAMES = [
        "init_db", "get_session", "get_session_factory",
        "SubscriberRepository", "SubscriberTopicRequestRepository",
        "SendHistoryRepository", "CollectedDataRepository",
        "NewsletterArchiveRepository", "EmailVerificationRepository",
        "BounceLogRepository", "SentArticleRepository",
        "CollectionMetricRepository",
        # 테스트가 직접 쓰는 반공개 이름
        "_migrate_subscriber_persona_columns",
        "_migrate_email_verification_signup_meta",
    ]

    def test_all_legacy_names_importable(self):
        mod = importlib.import_module("src.common.database.repository")
        missing = [n for n in self.LEGACY_NAMES if not hasattr(mod, n)]
        assert missing == []

    def test_engine_owns_session_lifecycle(self):
        from src.common.database import engine
        assert hasattr(engine, "init_db")
        assert hasattr(engine, "get_session")


class TestTenantDiscovery:
    def test_discovers_three_tenants(self):
        registered = discover_and_register()
        assert registered == ["allergy-insight", "standup", "tech-briefing"]
        reg = get_registry()
        assert {t.tenant_id for t in reg.get_all()} >= set(registered)

    def test_rerun_is_idempotent(self):
        first = discover_and_register()
        second = discover_and_register()
        assert first == second


class TestTenantSettingsEnvMapping:
    """이관된 필드가 기존 env 변수명으로 계속 로드되는지 (배포 env 호환)."""

    def test_allergy_env_names(self, monkeypatch):
        monkeypatch.setenv("ALLERGY_COLLECT_HOUR", "7")
        monkeypatch.setenv("ALLERGY_INSIGHT_NEWSLETTER_KEY", "k-123")
        monkeypatch.setenv("ALLERGY_INSIGHT_API_URL", "http://x:9040")
        from src.tenant.allergy_insight.config import AllergyInsightSettings
        s = AllergyInsightSettings()
        assert s.allergy_collect_hour == 7
        assert s.allergy_insight_newsletter_api_key == "k-123"
        assert s.allergy_insight_api_url == "http://x:9040"

    def test_tech_env_names(self, monkeypatch):
        monkeypatch.setenv("SKILLRADAR_NEWSLETTER_KEY", "sr-key")
        monkeypatch.setenv("TECH_BRIEFING_LLM_TOP_N", "9")
        from src.tenant.tech_briefing.config import TechBriefingSettings
        s = TechBriefingSettings()
        assert s.skillradar_newsletter_key == "sr-key"
        assert s.tech_briefing_llm_top_n == 9

    def test_standup_env_names(self, monkeypatch):
        monkeypatch.setenv("STANDUP_WEEKLY_DAY_OF_WEEK", "tue")
        from src.tenant.standup.config import StandUpSettings
        s = StandUpSettings()
        assert s.standup_weekly_day_of_week == "tue"

    def test_global_settings_no_longer_carries_tenant_fields(self):
        from src.config import settings
        for gone in ("allergy_collect_hour", "standup_api_url",
                     "tech_briefing_llm_model"):
            assert not hasattr(settings, gone)
