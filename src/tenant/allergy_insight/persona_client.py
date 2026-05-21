"""AllergyInsight 페르소나 적응형 뉴스레터 API 클라이언트.

엔드포인트: /api/public/newsletter/*  ·  인증 헤더: X-Newsletter-Key
정본 계약: Claude-Opus-bluevlad
  services/allergyinsight/plans/persona-adaptive-newsletter-plan.md §3.2
NLP 측 사양: PERSONA_ADAPTIVE_NEWSLETTER_SPEC.md / _N1_N2_DESIGN.md

graceful degrade 계약:
  - api_key 미설정(enabled=False) → 호출 자체를 하지 않고 폴백값 반환.
  - 백엔드 503(서버측 키 미구성)/401/네트워크 오류 → 동일하게 폴백값 반환.
  - 어느 경우에도 예외를 호출부로 전파하지 않는다 (구독 플로우를 막지 않음).

N2 에서 request_topic() / get_job() 가 추가된다.
"""

import logging
import time
from typing import Optional

import httpx

from ...config import settings

logger = logging.getLogger(__name__)

API_TIMEOUT = 60.0
# 페르소나 카탈로그는 구독 페이지 로드 경로 — 백엔드 지연이 페이지를 막지 않도록
# 짧은 타임아웃 + 재시도 없음 + TTL 캐시.
_CATALOG_TIMEOUT = 10.0
_PERSONA_CACHE_TTL = 600.0  # 10분
# trust_env=False: OrbStack 런타임이 컨테이너에 주입하는 NO_PROXY IPv6 CIDR 가
# httpx URL 파서를 깨뜨리는 문제 회피 (collector.py 와 동일 패턴).

# persona_code NULL 구독자의 런타임 폴백 페르소나.
DEFAULT_PERSONA = "patient"

# v1 정적 관심 알러젠 카탈로그 (N1_N2_DESIGN §11 Q1).
# code 는 AllergyInsight AllergenMaster.code 와 일치해야 N3 리랭킹이 동작한다.
INTEREST_ALLERGENS = [
    {"code": "peanut", "label": "땅콩"},
    {"code": "milk", "label": "우유"},
    {"code": "egg", "label": "계란"},
    {"code": "tree_nut", "label": "견과류"},
    {"code": "shellfish", "label": "갑각류·어패류"},
    {"code": "wheat", "label": "밀"},
    {"code": "soy", "label": "대두"},
    {"code": "fruit", "label": "과일"},
]

# 모듈 단위 페르소나 카탈로그 캐시 (성공 응답만 캐싱).
_persona_cache: dict = {"data": None, "ts": 0.0}


def persona_default_depth(personas: list[dict], code: str) -> str:
    """페르소나 카탈로그에서 code 의 default_depth 조회. 미발견 시 'practical'."""
    for p in personas:
        if p.get("code") == code:
            return p.get("default_depth") or "practical"
    return "practical"


class PersonaNewsletterClient:
    """AllergyInsight 페르소나 뉴스레터 API 클라이언트."""

    def __init__(
        self,
        api_base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.api_base_url = (
            api_base_url or settings.allergy_insight_api_url
        ).rstrip("/")
        self.api_key = (
            api_key if api_key is not None
            else settings.allergy_insight_newsletter_api_key
        )

    @property
    def enabled(self) -> bool:
        """인증 키가 설정되어 있으면 True. False 면 호출부는 폴백 경로로 동작."""
        return bool(self.api_key)

    def _headers(self) -> dict:
        return {"X-Newsletter-Key": self.api_key}

    async def get_personas(self, use_cache: bool = True) -> list[dict]:
        """GET /api/public/newsletter/personas — 페르소나 카탈로그.

        Returns:
            페르소나 dict 리스트. 각 항목:
              {code, label, description, default_depth, recommended_sections}
            키 미설정·호출 실패 시 빈 리스트 [] (UI 는 페르소나 단계를 숨김).
        """
        if not self.enabled:
            logger.debug("persona_client 비활성 (api_key 미설정) — get_personas 스킵")
            return []

        now = time.monotonic()
        if (
            use_cache
            and _persona_cache["data"] is not None
            and now - _persona_cache["ts"] < _PERSONA_CACHE_TTL
        ):
            return _persona_cache["data"]

        url = f"{self.api_base_url}/api/public/newsletter/personas"
        try:
            async with httpx.AsyncClient(
                timeout=_CATALOG_TIMEOUT, trust_env=False
            ) as client:
                response = await client.get(url, headers=self._headers())
                response.raise_for_status()
                data = response.json()
            personas = (data or {}).get("data", {}).get("personas", [])
            _persona_cache["data"] = personas
            _persona_cache["ts"] = now
            logger.info(f"페르소나 카탈로그 {len(personas)}종 수신")
            return personas
        except Exception as e:
            logger.warning(f"get_personas 실패 — 폴백([]): {e}")
            return []
