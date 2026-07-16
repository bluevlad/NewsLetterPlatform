"""TechBriefing analyzer 프롬프트.

설계 원칙 (StandUp prompts.py 차용):
- 한국어 출력 강제
- "모르면 모른다" — 입력 자료 외 정보 만들어내지 말 것
- JSON 외 출력 금지 (parser 가 가장 큰 {...} 블록만 추출하지만 안전을 위해 명시)
- 4 섹션 고정 스키마 (what_it_is / who_benefits / recommendation / action_tip)

도메인: AI 학습·커리어 (교육과정/세미나/정책/뉴스) — 독자는 취업준비생과
주니어·시니어 직장인.
"""

from __future__ import annotations

ANALYZE_SYSTEM = """\
당신은 한국어로 답하는 AI 학습·커리어 큐레이터입니다.
역할: AI 관련 소식(교육과정 모집·세미나·정부 정책·뉴스)을 독자
       (취업준비생, 주니어·시니어 직장인) 관점에서 평가하여, 참여/활용 여부를
       즉시 판단할 수 있게 4섹션 JSON으로 답합니다.

엄격한 규칙:
1. **출력은 JSON 객체 단 1개. JSON 외 어떤 prose/마크다운/코드펜스 금지.**
2. **입력 자료(title/summary)에 없는 사실(모집 기간, 비용, 장소, 주최기관 등)을
   만들어내지 말 것.** 모르면 "상세 내용은 원문 확인 필요" 같이 솔직히.
3. 기관명·과정명·정책명은 원문 그대로 유지.
4. 권장 등급(level)은 정확히 다음 4개 중 하나:
   - "APPLY" — 지금 신청/확인 권장 (모집 중이거나 마감 임박, 명백한 기회)
   - "PLAN"  — 일정을 확인하고 참여 계획 수립 권장
   - "WATCH" — 후속 소식 관찰 (아직 구체적 행동 불필요)
   - "SKIP"  — 독자 대상과 관련성 낮음
5. 모든 텍스트 필드 한국어. 각 필드 길이 가이드:
   - what_it_is: 1~2문장 (무슨 소식인지 핵심만)
   - who_benefits: 1~2문장 (취준생/주니어/시니어 중 누구에게 왜 유용한지)
   - recommendation.rationale: 1~2문장 (왜 그 등급인지)
   - action_tip: 1문장 (신청 방법·마감·확인 포인트 등 다음 행동)

JSON 스키마:
{
  "what_it_is": "한국어 1~2문장",
  "who_benefits": "한국어 1~2문장 — 대상 독자와 연결",
  "recommendation": {
    "level": "APPLY" | "PLAN" | "WATCH" | "SKIP",
    "rationale": "한국어 1~2문장"
  },
  "action_tip": "한국어 1문장"
}
"""


ANALYZE_USER_TPL = """\
[독자 프로필]
- 취업준비생: AI 직무 취업을 위한 교육과정·국비지원 훈련·자격에 관심
- 주니어 직장인: 실무 역량 강화 강의·세미나·커뮤니티에 관심
- 시니어 직장인: 직무 전환·리스킬링 정책·중장년 지원사업에 관심

[평가 대상 소식]
category: {category}
keyword: {keyword}
origin: {origin}
title: {title}
{recruiting_block}url: {url}
published: {published}
summary:
{summary}

위 소식을 독자 관점에서 평가하여 JSON 1개로 답하세요.
"""


def render_user_prompt(*, item: dict) -> str:
    """analyzer 가 호출하는 user prompt 렌더."""
    recruiting_block = ""
    if item.get("is_recruiting"):
        recruiting_block = "recruiting: 제목에 모집/신청/접수/마감 신호 있음\n"

    return ANALYZE_USER_TPL.format(
        category=item.get("category", ""),
        keyword=item.get("keyword", ""),
        origin=item.get("origin", ""),
        title=item.get("title", ""),
        recruiting_block=recruiting_block,
        url=item.get("url", ""),
        published=str(item.get("published_at") or ""),
        summary=(item.get("summary") or "(없음)")[:1500],
    )
