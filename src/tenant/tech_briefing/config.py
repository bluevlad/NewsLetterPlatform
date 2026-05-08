"""TechBriefing 테넌트 설정 — Java/React 일일 기술 브리핑.

3 sources (MVP 풀세트):
  1. GitHub Releases — 핵심 OSS 프로젝트의 최신 릴리즈
  2. NVD CVE feed — Java/JS 생태 보안 공시
  3. RSS — 공식 블로그 (Spring · React · Kotlin · TypeScript · Next.js · Vite)
"""

from ..base import BrandConfig, BrandFeature

TENANT_ID = "tech-briefing"
DISPLAY_NAME = "TechBriefing Java/React 일일 브리핑"
EMAIL_SUBJECT_PREFIX = "[TechBriefing]"
EMAIL_TEMPLATE = "tech_briefing/daily_report.html"


# ─── 큐레이션 풀: 3 tier (스코어 가중치에 사용) ─────────────────────
# tier_s = 1.0 (생태 핵심), tier_a = 0.7, tier_b = 0.4
# (owner, repo, ecosystem, tier)
GITHUB_REPOS: list[tuple[str, str, str, str]] = [
    # tier S — 생태계 중심
    ("spring-projects", "spring-boot",      "java-be",     "S"),
    ("spring-projects", "spring-framework", "java-be",     "S"),
    ("facebook",        "react",            "react-core",  "S"),
    ("microsoft",       "TypeScript",       "language",    "S"),
    ("JetBrains",       "kotlin",           "language",    "S"),
    ("nodejs",          "node",             "runtime",     "S"),

    # tier A — 핵심 부품
    ("vercel",          "next.js",          "react-meta",  "A"),
    ("vitejs",          "vite",             "tooling",     "A"),
    ("spring-projects", "spring-cloud",     "java-be",     "A"),
    ("spring-projects", "spring-security",  "java-be",     "A"),
    ("remix-run",       "react-router",     "react-core",  "A"),
    ("TanStack",        "query",            "react-state", "A"),
    ("gradle",          "gradle",           "tooling",     "A"),
    ("apache",          "maven",            "tooling",     "A"),

    # tier B — 보조 (있으면 보조 표시)
    ("tailwindlabs",    "tailwindcss",      "styling",     "B"),
    ("pmndrs",          "zustand",          "react-state", "B"),
    ("storybookjs",     "storybook",        "tooling",     "B"),
    ("hibernate",       "hibernate-orm",    "java-be",     "B"),
    ("quarkusio",       "quarkus",          "java-be",     "B"),
    ("micronaut-projects", "micronaut-core","java-be",     "B"),
]

PROJECT_TIER_WEIGHT = {"S": 1.0, "A": 0.7, "B": 0.45}


# ─── RSS 풀 (공식 블로그 우선) ──────────────────────────────────────
# (label, url, ecosystem, tier)
RSS_FEEDS: list[tuple[str, str, str, str]] = [
    ("Spring Blog",      "https://spring.io/blog.atom",                                       "java-be",    "S"),
    ("React Blog",       "https://react.dev/rss.xml",                                         "react-core", "S"),
    ("Kotlin Blog",      "https://blog.jetbrains.com/kotlin/feed/",                           "language",   "S"),
    ("TypeScript Blog",  "https://devblogs.microsoft.com/typescript/feed/",                   "language",   "S"),
    ("Next.js Blog",     "https://nextjs.org/feed.xml",                                       "react-meta", "A"),
    ("Vite Blog",        "https://vite.dev/blog.rss",                                         "tooling",    "A"),
]


# ─── NVD CVE 키워드 ────────────────────────────────────────────────
# Java/JS 생태 핵심 키워드. NVD keywordSearch는 OR 가능하지만 단일 호출로
# 처리하기 위해 다중 키워드를 순차 호출 → 결과 병합.
NVD_KEYWORDS: list[str] = [
    "spring framework",
    "spring boot",
    "react",
    "node.js",
    "typescript",
    "next.js",
    "kotlin",
    "tomcat",
    "log4j",
]

# NVD 조회 윈도(일).
NVD_LOOKBACK_DAYS = 14

# GitHub Releases 조회 per-repo per_page.
GITHUB_PER_PAGE = 5


# ─── 카테고리(ecosystem) → 한글 라벨/색상 ─────────────────────────
ECOSYSTEM_META: dict[str, dict[str, str]] = {
    "java-be":    {"label": "Backend/Java",   "color": "#c2410c", "bg": "#ffedd5"},
    "react-core": {"label": "React Core",     "color": "#0e7490", "bg": "#cffafe"},
    "react-state":{"label": "React State",    "color": "#0369a1", "bg": "#e0f2fe"},
    "react-meta": {"label": "Meta-Framework", "color": "#1d4ed8", "bg": "#dbeafe"},
    "language":   {"label": "Language/Eco",   "color": "#7c3aed", "bg": "#ede9fe"},
    "runtime":    {"label": "Runtime",        "color": "#15803d", "bg": "#dcfce7"},
    "tooling":    {"label": "Tooling",        "color": "#a16207", "bg": "#fef9c3"},
    "styling":    {"label": "Styling",        "color": "#be185d", "bg": "#fce7f3"},
}

# Brand: blue/cyan 톤 (Java orange + React cyan 보색 조합 절충)
BRAND_CONFIG = BrandConfig(
    primary_color="#1d4ed8",
    primary_color_dark="#1e3a8a",
    accent_color="#06b6d4",
    logo_text="TechBriefing",
    tagline="Java · React 일일 브리핑",
    description="Java/React 생태계의 릴리즈·보안·키워드 트렌드를 매일 아침 이메일로 받아보세요",
    features=[
        BrandFeature(
            icon="&#x1F3AF;",
            title="오늘의 헤드라인",
            description="공식 블로그 + GitHub 릴리즈 + CVE를 합성한 핵심 5건",
        ),
        BrandFeature(
            icon="&#x1F680;",
            title="릴리즈 & 보안",
            description="새 버전 / Breaking Changes / CVE / Deprecation 4분류",
        ),
        BrandFeature(
            icon="&#x1F4C8;",
            title="키워드 트렌드",
            description="상승·하락 키워드와 동반 빈출 키워드",
        ),
        BrandFeature(
            icon="&#x1F4DA;",
            title="공식 출처 우선",
            description="Spring·React·Kotlin·TypeScript·Next.js·Vite 공식 블로그",
        ),
    ],
)
