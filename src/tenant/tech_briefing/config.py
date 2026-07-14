"""TechBriefing 테넌트 설정 — AI/LLM 일일 기술 브리핑.

3 sources (MVP 풀세트):
  1. GitHub Releases — 핵심 AI OSS 프로젝트의 최신 릴리즈
  2. NVD CVE feed — AI 툴체인 + 운영 스택(Spring/Tomcat) 보안 공시
  3. RSS — 공식 블로그 (OpenAI · Hugging Face · Google AI · DeepMind · PyTorch · Ollama)

2026-07 도메인 전환: Java/React → AI/LLM. NVD 키워드에는 운영 서비스
(HopenVision 등 Spring 스택) 보안 조기 경보를 위해 운영 스택 키워드를 잔존시킨다.
"""

from ..base import BrandConfig, BrandFeature

TENANT_ID = "tech-briefing"
DISPLAY_NAME = "TechBriefing AI/LLM 일일 브리핑"
EMAIL_SUBJECT_PREFIX = "[TechBriefing]"
EMAIL_TEMPLATE = "tech_briefing/daily_report.html"


# ─── 큐레이션 풀: 3 tier (스코어 가중치에 사용) ─────────────────────
# tier_s = 1.0 (생태 핵심), tier_a = 0.7, tier_b = 0.4
# (owner, repo, ecosystem, tier)
# 주의: ggml-org/llama.cpp 는 커밋 단위 자동 릴리즈(b####)로 잡음이 커 제외.
#       로컬 LLM 신호는 ollama / mlx / open-webui 로 커버.
GITHUB_REPOS: list[tuple[str, str, str, str]] = [
    # tier S — 생태계 중심 (mlx 는 MacBook 로컬 LLM 운영 스택이라 S 승격)
    ("pytorch",         "pytorch",           "ml-framework", "S"),
    ("huggingface",     "transformers",      "ml-framework", "S"),
    ("vllm-project",    "vllm",              "inference",    "S"),
    ("ollama",          "ollama",            "local-llm",    "S"),
    ("langchain-ai",    "langchain",         "agent",        "S"),
    ("ml-explore",      "mlx",               "local-llm",    "S"),

    # tier A — 핵심 부품
    ("openai",          "openai-python",     "llm-api",      "A"),
    ("anthropics",      "anthropic-sdk-python", "llm-api",   "A"),
    ("googleapis",      "python-genai",      "llm-api",      "A"),
    ("run-llama",       "llama_index",       "agent",        "A"),
    ("sgl-project",     "sglang",            "inference",    "A"),
    ("BerriAI",         "litellm",           "llm-ops",      "A"),
    ("pydantic",        "pydantic-ai",       "agent",        "A"),

    # tier B — 보조 (있으면 보조 표시)
    ("huggingface",     "peft",              "ml-framework", "B"),
    ("unslothai",       "unsloth",           "ml-framework", "B"),
    ("gradio-app",      "gradio",            "tooling",      "B"),
    ("langfuse",        "langfuse",          "llm-ops",      "B"),
    ("mlflow",          "mlflow",            "llm-ops",      "B"),
    ("open-webui",      "open-webui",        "local-llm",    "B"),
]

PROJECT_TIER_WEIGHT = {"S": 1.0, "A": 0.7, "B": 0.45}


# ─── RSS 풀 (공식 블로그 우선) ──────────────────────────────────────
# (label, url, ecosystem, tier)
# URL 검증: 2026-07-14 curl 200 확인. Anthropic·Meta AI·vLLM 블로그는 RSS 미제공으로 제외.
RSS_FEEDS: list[tuple[str, str, str, str]] = [
    ("OpenAI News",        "https://openai.com/news/rss.xml",          "llm-api",      "S"),
    ("Hugging Face Blog",  "https://huggingface.co/blog/feed.xml",     "ml-framework", "S"),
    ("Google AI Blog",     "https://blog.google/technology/ai/rss/",   "llm-api",      "S"),
    ("DeepMind Blog",      "https://deepmind.google/blog/rss.xml",     "research",     "A"),
    ("PyTorch Blog",       "https://pytorch.org/blog/feed/",           "ml-framework", "A"),
    ("Ollama Blog",        "https://ollama.com/blog/rss.xml",          "local-llm",    "A"),
]


# ─── NVD CVE 키워드 ────────────────────────────────────────────────
# NVD keywordSearch는 OR 가능하지만 단일 호출로 처리하기 위해
# 다중 키워드를 순차 호출 → 결과 병합.
# 앞 8개: AI 툴체인 / 뒤 3개: 운영 서비스 스택(HopenVision — Spring Boot/Tomcat)
# 보안 조기 경보용 잔존분. Service Profile 🎯 매칭이 이 키워드들에 의존한다.
NVD_KEYWORDS: list[str] = [
    "pytorch",
    "langchain",
    "ollama",
    "vllm",
    "gradio",
    "mlflow",
    "hugging face",
    "open webui",
    "spring framework",
    "spring boot",
    "tomcat",
]

# NVD 조회 윈도(일).
NVD_LOOKBACK_DAYS = 14

# GitHub Releases 조회 per-repo per_page.
GITHUB_PER_PAGE = 5


# ─── 카테고리(ecosystem) → 한글 라벨/색상 ─────────────────────────
# java-be 는 운영 스택 CVE 잔존 키워드(spring/tomcat) 매핑용으로 유지.
ECOSYSTEM_META: dict[str, dict[str, str]] = {
    "ml-framework": {"label": "ML Framework",     "color": "#c2410c", "bg": "#ffedd5"},
    "inference":    {"label": "Inference/Serving","color": "#0e7490", "bg": "#cffafe"},
    "local-llm":    {"label": "Local LLM",        "color": "#15803d", "bg": "#dcfce7"},
    "agent":        {"label": "Agent/RAG",        "color": "#7c3aed", "bg": "#ede9fe"},
    "llm-api":      {"label": "LLM API",          "color": "#1d4ed8", "bg": "#dbeafe"},
    "llm-ops":      {"label": "LLMOps",           "color": "#0369a1", "bg": "#e0f2fe"},
    "research":     {"label": "Research",         "color": "#be185d", "bg": "#fce7f3"},
    "tooling":      {"label": "Tooling",          "color": "#a16207", "bg": "#fef9c3"},
    "java-be":      {"label": "운영스택/Java",     "color": "#57534e", "bg": "#f5f5f4"},
}

# Brand: blue/cyan 톤 유지 (템플릿 인라인 스타일과 결합)
BRAND_CONFIG = BrandConfig(
    primary_color="#1d4ed8",
    primary_color_dark="#1e3a8a",
    accent_color="#06b6d4",
    logo_text="TechBriefing",
    tagline="AI · LLM 일일 브리핑",
    description="AI/LLM 생태계의 릴리즈·보안·키워드 트렌드를 매일 아침 이메일로 받아보세요",
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
            description="OpenAI·Hugging Face·Google AI·DeepMind·PyTorch·Ollama 공식 블로그",
        ),
    ],
)
