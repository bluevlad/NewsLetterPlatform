"""TechBriefing digest 고도화 테스트.

#1 릴리즈 버전 통합(_consolidate_releases)
#2 보안 CVE 적용/기타 분리(_split_cves_by_relevance + format)
"""

from datetime import datetime

import pytest

from src.config import settings
from src.tenant.tech_briefing.formatter import TechBriefingFormatter


@pytest.fixture(autouse=True)
def _disable_llm():
    """digest 테스트는 LLM 불필요 — Ollama 호출 차단."""
    orig_t = settings.tech_briefing_translate_enabled
    orig_l = settings.tech_briefing_llm_enabled
    settings.tech_briefing_translate_enabled = False
    settings.tech_briefing_llm_enabled = False
    yield
    settings.tech_briefing_translate_enabled = orig_t
    settings.tech_briefing_llm_enabled = orig_l


def _release(project_short, tag, published, *, breaking=False,
             deprecation=False, score=6.0):
    return {
        "source": "github_release",
        "project": f"org/{project_short}",
        "project_short": project_short,
        "tag": tag,
        "title": f"{project_short} {tag}",
        "ecosystem": "tooling",
        "tier": "B",
        "published_at": published,
        "summary": "",
        "is_breaking": breaking,
        "has_deprecation": deprecation,
        "importance_score": score,
        "relevance_max": 0.0,
        "dedup_key": f"gh-release:org/{project_short}:{tag}",
    }


def _cve(idx, ecosystem):
    return {
        "source": "nvd_cve",
        "project": f"cve-proj-{idx}",
        "project_short": f"p{idx}",
        "ecosystem": ecosystem,
        "tier": "S",
        "title": f"CVE-2026-X{idx}",
        "cve_id": f"CVE-2026-X{idx}",
        "summary": "",
        "cvss": 9.5,
        "severity": "critical",
        "matched_keyword": "x",
        "published_at": datetime(2026, 5, 19),
        "dedup_key": f"cve:CVE-2026-X{idx}",
    }


# ─── #1 릴리즈 버전 통합 ───────────────────────────────────────────

def test_consolidate_releases_groups_by_project():
    """같은 프로젝트의 여러 버전 → 1개 통합 엔트리."""
    fmt = TechBriefingFormatter()
    items = [
        _release("hibernate-orm", "7.3.5", datetime(2026, 5, 19), score=6.0),
        _release("hibernate-orm", "7.3.4", datetime(2026, 5, 15),
                 breaking=True, score=6.5),
        _release("hibernate-orm", "7.3.3", datetime(2026, 5, 10), score=5.0),
        _release("react", "19.2.0", datetime(2026, 5, 18),
                 deprecation=True, score=7.0),
    ]
    result = fmt._consolidate_releases(items)

    assert len(result) == 2  # 2개 프로젝트로 통합
    by_proj = {e["project_short"]: e for e in result}

    hib = by_proj["hibernate-orm"]
    assert hib["version_count"] == 3
    assert hib["extra_count"] == 2
    assert hib["tag"] == "7.3.5"            # 대표 = 최신 발행
    assert hib["group_breaking"] is True    # 7.3.4 가 breaking → 그룹 OR
    assert hib["group_deprecation"] is False

    react = by_proj["react"]
    assert react["extra_count"] == 0
    assert react["group_deprecation"] is True

    # 그룹 정렬 — react(7.0) > hibernate-orm(max 6.5)
    assert result[0]["project_short"] == "react"


def test_consolidate_releases_empty():
    assert TechBriefingFormatter()._consolidate_releases([]) == []


# ─── #2 보안 CVE 적용/기타 분리 ──────────────────────────────────

def test_split_cves_by_relevance():
    """service_relevance 매칭 여부로 적용/기타 분리, 순서 보존."""
    applied_cve = {"cve_id": "CVE-A",
                   "service_relevance": {"hopenvision": {"score": 4.0,
                                                         "reason": "관심: Tomcat"}}}
    zero_score = {"cve_id": "CVE-B",
                  "service_relevance": {"hopenvision": {"score": 0.0}}}
    no_key = {"cve_id": "CVE-C"}

    applied, other = TechBriefingFormatter._split_cves_by_relevance(
        [applied_cve, zero_score, no_key]
    )
    assert [c["cve_id"] for c in applied] == ["CVE-A"]
    assert [c["cve_id"] for c in other] == ["CVE-B", "CVE-C"]


def test_format_digest_structure():
    """format() — 릴리즈는 프로젝트 통합, CVE는 적용/기타 그룹 분리."""
    fmt = TechBriefingFormatter()
    # Tomcat(hopenvision high_interest) 매칭 CVE 7개 — 서로 다른 ecosystem.
    # relevance 부스트로 점수가 높아 5개는 헤드라인, 2개는 digest "적용 프로젝트".
    ecos = ["java-be", "react-core", "react-state", "react-meta",
            "language", "runtime", "tooling"]
    tomcat_cves = []
    for i, eco in enumerate(ecos):
        tomcat_cves.append({
            "source": "nvd_cve", "project": f"tomcat-{i}",
            "project_short": f"tomcat-{i}", "ecosystem": eco, "tier": "S",
            "title": f"CVE-2026-T{i} Apache Tomcat flaw",
            "cve_id": f"CVE-2026-T{i}",
            "summary": "Apache Tomcat vulnerability",
            "cvss": 7.0, "severity": "high", "matched_keyword": "tomcat",
            "published_at": datetime(2026, 5, 19),
            "dedup_key": f"cve:CVE-2026-T{i}",
        })
    # express CVE — hopenvision 미매칭 → digest "기타".
    express_cve = {
        "source": "nvd_cve", "project": "express", "project_short": "express",
        "ecosystem": "styling", "tier": "S",
        "title": "CVE-2026-8888 express flaw", "cve_id": "CVE-2026-8888",
        "summary": "A web framework path traversal issue",
        "cvss": 5.0, "severity": "medium", "matched_keyword": "express",
        "published_at": datetime(2026, 5, 19), "dedup_key": "cve:CVE-2026-8888",
    }
    releases = [
        _release("hibernate-orm", "7.3.5", datetime(2026, 5, 19)),
        _release("hibernate-orm", "7.3.4", datetime(2026, 5, 15)),
        _release("react", "19.2.0", datetime(2026, 5, 18)),
    ]
    payload = {"tech_daily": {
        "github_releases": releases,
        "cves": tomcat_cves + [express_cve],
        "rss_articles": [],
        "report_date": "2026-05-20",
        "stats": {},
    }}
    ctx = fmt.format(payload)
    groups = {g["label"]: g for g in ctx["digest_groups"]}

    # #1 — 릴리즈 3건이 2개 프로젝트로 통합
    assert "릴리즈" in groups
    rel_entries = groups["릴리즈"]["entries"]
    assert len(rel_entries) == 2
    hib = next(e for e in rel_entries if e["project_short"] == "hibernate-orm")
    assert hib["extra_count"] == 1

    # #2 — 보안 적용/기타 그룹 분리
    assert "🎯 보안 · 적용 프로젝트" in groups
    assert "보안 · 기타" in groups
    applied_ids = {e["cve_id"] for e in groups["🎯 보안 · 적용 프로젝트"]["entries"]}
    other_ids = {e["cve_id"] for e in groups["보안 · 기타"]["entries"]}
    # 적용 프로젝트 = Tomcat 매칭 CVE 잔여분
    assert applied_ids and all(cid.startswith("CVE-2026-T") for cid in applied_ids)
    # 기타 = 미매칭 express
    assert "CVE-2026-8888" in other_ids
