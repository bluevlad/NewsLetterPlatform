"""SkillRadar 커리큘럼 공급(/api/v1/newsletter/curriculum) → '오늘의 학습' 섹션 컨텍스트.

Phase 1 계약 (SkillRadar docs/roadmap/DAILY_CURRICULUM_DESIGN.md §14):
  - 메일에는 트랙별 테마·포커스·레슨 제목·소요분·딥링크만 싣는다. 본문은 웹(/learn).
  - 정답·해설은 SkillRadar 가 애초에 내려주지 않는다.
  - payload 가 없거나 fallback 이면 섹션 자체를 생략 (None).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

TRACK_LABEL = {"newcomer": "신입 트랙", "senior": "시니어 트랙"}
TRACK_COLOR = {"newcomer": ("#1d4ed8", "#eff6ff"), "senior": ("#047857", "#ecfdf5")}
KIND_LABEL = {"read": "읽기", "practice": "실습", "quiz": "확인 퀴즈", "trend": "동향"}
KIND_ICON = {"read": "📖", "practice": "🛠", "quiz": "✅", "trend": "📡"}


def _lesson(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    kind = raw.get("kind")
    title = (raw.get("title") or "").strip()
    link = raw.get("link")
    if kind not in KIND_LABEL or not title or not link:
        return None
    extra = ""
    if kind == "quiz" and raw.get("n_questions"):
        extra = f"{raw['n_questions']}문항"
    elif kind == "trend" and isinstance(raw.get("items"), list):
        extra = f"{len(raw['items'])}건"
    return {
        "kind": kind, "kind_label": KIND_LABEL[kind], "icon": KIND_ICON[kind],
        "title": title, "est_min": int(raw.get("est_min") or 0), "link": link, "extra": extra,
    }


def build_curriculum_context(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """collector 의 curriculum payload → 템플릿 컨텍스트. 없거나 비면 None."""
    if not payload or payload.get("fallback") or not payload.get("tracks"):
        return None
    tracks: List[Dict[str, Any]] = []
    for t in payload.get("tracks") or []:
        track = t.get("track")
        if track not in TRACK_LABEL or t.get("status") == "failed":
            continue
        lessons = [x for x in (_lesson(r) for r in t.get("lessons") or []) if x]
        if not lessons:
            continue
        color, bg = TRACK_COLOR[track]
        tracks.append({
            "track": track, "label": TRACK_LABEL[track], "color": color, "bg": bg,
            "theme_title": t.get("theme_title") or "오늘의 학습",
            "focus": t.get("focus"), "week": t.get("cohort_week"),
            "partial": t.get("status") == "fallback",
            "total_min": sum(l["est_min"] for l in lessons),
            "lessons": lessons,
            "link": lessons[0]["link"],
        })
    if not tracks:
        return None
    return {"date": payload.get("date"), "tracks": tracks}
