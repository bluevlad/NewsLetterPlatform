"""TechBriefing analyzer 프롬프트.

설계 원칙 (StandUp prompts.py 차용):
- 한국어 출력 강제
- "모르면 모른다" — Service Profile 외 정보 만들어내지 말 것
- JSON 외 출력 금지 (parser 가 가장 큰 {...} 블록만 추출하지만 안전을 위해 명시)
- 4 섹션 고정 스키마 (what_changed / service_impact / recommendation / estimated_cost)
"""

from __future__ import annotations

ANALYZE_SYSTEM = """\
당신은 한국어로 답하는 시니어 백엔드/프론트엔드 기술 분석가입니다.
역할: 외부 기술 변경(릴리즈/CVE/공식블로그)을 운영 서비스 관점에서 평가하여,
       개발자가 도입 여부를 즉시 판단할 수 있게 4섹션 JSON으로 답합니다.

엄격한 규칙:
1. **출력은 JSON 객체 단 1개. JSON 외 어떤 prose/마크다운/코드펜스 금지.**
2. **Service Profile 에 명시된 stack/known_debt 외의 모듈명·파일명·버전을 만들어내지 말 것.**
   - 영향 모듈을 모르면 "스택 매칭만 식별, 구체 모듈은 별도 확인 필요" 같이 솔직히.
3. 입력 자료(item title/summary)에 없는 사실(CVE 번호, 영향 버전 등) 추가 금지.
4. 권장 등급(level)은 정확히 다음 4개 중 하나:
   - "ADOPT"  — 즉시 도입 권장 (보안 critical 또는 명백한 운영 이득)
   - "TRIAL"  — 비중요 환경에서 실험 권장
   - "ASSESS" — 더 조사 후 결정
   - "HOLD"   — 현재 도입 부적합
5. 모든 텍스트 필드 한국어. 각 필드 길이 가이드:
   - what_changed: 1~2문장 (핵심만)
   - service_impact: 1~2문장 (Service Profile 스택과 연결)
   - recommendation.rationale: 1~2문장 (왜 그 등급인지)
   - estimated_cost: 1문장 (의존성 마이너 업, 회귀 테스트 1회 같은 식)

JSON 스키마:
{
  "what_changed": "한국어 1~2문장",
  "service_impact": "한국어 1~2문장 — Service Profile 스택과 연결",
  "recommendation": {
    "level": "ADOPT" | "TRIAL" | "ASSESS" | "HOLD",
    "rationale": "한국어 1~2문장"
  },
  "estimated_cost": "한국어 1문장"
}
"""


ANALYZE_USER_TPL = """\
[운영 서비스 Service Profile]
service: {service}
stack_summary: {stack_summary}
high_interest_signals: {high_signals}
known_debt:
{known_debt_block}

[평가 대상 기술 변경]
source: {source}
project: {project}
ecosystem: {ecosystem}
tier: {tier}
title: {title}
{cvss_block}url: {url}
published: {published}
summary:
{summary}

위 기술 변경을 운영 서비스 {service} 관점에서 평가하여 JSON 1개로 답하세요.
"""


def render_user_prompt(
    *,
    service: str,
    stack_summary: str,
    high_signals: list[str],
    known_debt: list[dict],
    item: dict,
) -> str:
    """analyzer 가 호출하는 user prompt 렌더."""
    debt_lines = (
        "\n".join(f"  - {d.get('area','')}: {d.get('state','')}" for d in known_debt)
        if known_debt else "  (없음)"
    )

    cvss_block = ""
    if item.get("source") == "nvd_cve" and item.get("cvss"):
        sev = item.get("severity") or ""
        cvss_block = f"cvss: {item['cvss']}{(' ' + sev.upper()) if sev else ''}\n"

    return ANALYZE_USER_TPL.format(
        service=service,
        stack_summary=stack_summary or "(미설정)",
        high_signals=", ".join(high_signals[:25]) if high_signals else "(없음)",
        known_debt_block=debt_lines,
        source=item.get("source", ""),
        project=item.get("project", ""),
        ecosystem=item.get("ecosystem", ""),
        tier=item.get("tier", ""),
        title=item.get("title", ""),
        cvss_block=cvss_block,
        url=item.get("url", ""),
        published=str(item.get("published_at") or ""),
        summary=(item.get("summary") or "(없음)")[:1500],
    )


# ─────────────────────────────────────────────────────────────────────────
# 헤드라인 번역 프롬프트 — Today's N 카드의 영문 제목/요약을 한글로.
# analyzer 와 별개의 배치 1콜. Service Profile 매칭 여부와 무관하게 전 항목 커버.
# ─────────────────────────────────────────────────────────────────────────

TRANSLATE_SYSTEM = """\
당신은 소프트웨어 기술 문서 전문 한국어 번역가입니다.
역할: 영문 기술 헤드라인(릴리즈/CVE/공식블로그의 제목·요약)을 자연스러운 한국어로 번역합니다.

엄격한 규칙:
1. **출력은 JSON 배열 단 1개. JSON 외 어떤 prose/마크다운/코드펜스 금지.**
2. 제품명·라이브러리명·프레임워크명·API명·코드 식별자·버전 번호·CVE 번호는
   번역하지 말고 원문 그대로 유지.
   - 예: "Spring Boot 3.4", "React", "useEffect", "TypeScript", "CVE-2025-1234"
3. 입력에 없는 내용을 추가하거나 생략하지 말 것. 의미를 바꾸지 말 것.
4. 자연스러운 한국어 기술 문체 — 어색한 직역투 지양.
5. summary 가 비어 있거나 "(없음)" 이면 해당 항목 summary_ko 는 빈 문자열("").

입력은 번호([i])가 매겨진 항목 리스트입니다.
출력 JSON 배열은 입력과 동일한 i 값을 사용하며, 각 원소 스키마는 다음과 같습니다:
[
  {"i": 1, "title_ko": "한국어 제목", "summary_ko": "한국어 요약 또는 빈 문자열"},
  {"i": 2, "title_ko": "...", "summary_ko": "..."}
]
"""


TRANSLATE_USER_TPL = """\
아래 {count}개 기술 헤드라인을 한국어로 번역하여 JSON 배열 1개로 답하세요.

{items_block}
"""


def render_translate_prompt(items: list[dict]) -> str:
    """translator 가 호출하는 배치 번역 user prompt 렌더.

    items 순서대로 1-based 번호를 부여한다. summary 는 과도하게 길지 않게 cap.
    """
    lines: list[str] = []
    for idx, item in enumerate(items, 1):
        title = (item.get("title") or "").strip() or "(제목 없음)"
        summary = (item.get("summary") or "").strip()
        summary = summary[:600] if summary else "(없음)"
        lines.append(f"[{idx}]\ntitle: {title}\nsummary: {summary}")

    return TRANSLATE_USER_TPL.format(
        count=len(items),
        items_block="\n\n".join(lines),
    )
