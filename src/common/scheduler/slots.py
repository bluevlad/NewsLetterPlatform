"""
발송 시간 슬롯 정의

사용자별 발송 시간을 3단계 슬롯으로 분리:
- early: 6:40 (아침형)
- mid:   7:40 (출근 전)
- late:  8:40 (출근 후, 기본값)

weekly/monthly 발송은 daily 슬롯에서 -10분 (좀 더 일찍 받기 위함).
"""

from typing import Dict, List, Optional


SlotDef = Dict[str, object]  # {"key": str, "hour": int, "minute": int, "label": str}


DAILY_SLOTS: List[SlotDef] = [
    {"key": "early", "hour": 6, "minute": 40, "label": "아침형 (6:40)"},
    {"key": "mid",   "hour": 7, "minute": 40, "label": "출근 전 (7:40)"},
    {"key": "late",  "hour": 8, "minute": 40, "label": "출근 후 (8:40)"},
]

DEFAULT_SLOT: str = "late"

# weekly/monthly는 daily 슬롯 시간에서 이 값(분)만큼 이동
WEEKLY_MONTHLY_OFFSET_MINUTES: int = -10


SLOT_KEYS: List[str] = [s["key"] for s in DAILY_SLOTS]


def get_slot(key: str) -> Optional[SlotDef]:
    for s in DAILY_SLOTS:
        if s["key"] == key:
            return s
    return None


def normalize_slot(slot: Optional[str]) -> str:
    """NULL/유효하지 않은 슬롯 → DEFAULT_SLOT"""
    if slot and slot in SLOT_KEYS:
        return slot
    return DEFAULT_SLOT


def get_slot_time(key: str, newsletter_type: str = "daily") -> tuple[int, int]:
    """슬롯 키 + 뉴스레터 유형에 따른 (hour, minute) 반환

    newsletter_type이 weekly/monthly면 -10분 적용.
    음수 분 처리: 7:30이 7:30 그대로, 6:40 - 10 = 6:30.
    """
    slot = get_slot(key) or get_slot(DEFAULT_SLOT)
    hour = int(slot["hour"])
    minute = int(slot["minute"])

    if newsletter_type in ("weekly", "monthly"):
        total = hour * 60 + minute + WEEKLY_MONTHLY_OFFSET_MINUTES
        hour = (total // 60) % 24
        minute = total % 60

    return hour, minute


def get_slots_for_template() -> List[Dict[str, object]]:
    """템플릿/UI용 슬롯 메타 (weekly/monthly 시간 미리계산 포함)"""
    result = []
    for s in DAILY_SLOTS:
        d_h, d_m = get_slot_time(s["key"], "daily")
        w_h, w_m = get_slot_time(s["key"], "weekly")
        result.append({
            "key": s["key"],
            "label": s["label"],
            "daily_time": f"{d_h:02d}:{d_m:02d}",
            "weekly_monthly_time": f"{w_h:02d}:{w_m:02d}",
            "is_default": s["key"] == DEFAULT_SLOT,
        })
    return result
