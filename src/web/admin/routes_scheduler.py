"""
Admin 스케줄러 모니터링
"""

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ...common.database.repository import get_session_factory, SubscriberRepository
from ...common.scheduler.health import HEALTH_FILE, check_health
from ...common.scheduler.slots import get_slots_for_template, DEFAULT_SLOT
from ...tenant.registry import get_registry
from ..shared import templates
from .auth import get_admin_or_redirect

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/admin/scheduler", response_class=HTMLResponse)
async def scheduler_page(request: Request):
    """스케줄러 모니터링 페이지"""
    redirect = get_admin_or_redirect(request)
    if redirect:
        return redirect

    registry = get_registry()
    tenants = registry.get_all()

    health_ok = check_health()
    health_data = {}
    if HEALTH_FILE.exists():
        try:
            health_data = json.loads(HEALTH_FILE.read_text())
        except Exception:
            pass

    slots_meta = get_slots_for_template()

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        # Build schedule info per tenant
        schedule_info = []
        for tenant in tenants:
            config = tenant.schedule_config
            slot_counts = SubscriberRepository.count_by_slot(db, tenant.tenant_id)
            info = {
                "tenant": tenant,
                "collect_time": f"{config['collect_hour']:02d}:{config['collect_minute']:02d}",
                "last_collect": health_data.get("collect", "N/A"),
                "last_send": health_data.get("send", "N/A"),
                "supported_frequencies": tenant.supported_frequencies,
                "slot_counts": slot_counts,
                "weekly_config": None,
                "monthly_config": None,
            }
            if "weekly" in tenant.supported_frequencies:
                wc = tenant.weekly_schedule_config
                if wc:
                    info["weekly_config"] = {
                        "day_of_week": wc.get("day_of_week", "mon"),
                        "collect_time": f"{wc.get('collect_hour', 5):02d}:{wc.get('collect_minute', 0):02d}",
                    }
            if "monthly" in tenant.supported_frequencies:
                mc = tenant.monthly_schedule_config
                if mc:
                    info["monthly_config"] = {
                        "day_of_month": mc.get("day_of_month", 1),
                        "collect_time": f"{mc.get('collect_hour', 5):02d}:{mc.get('collect_minute', 0):02d}",
                    }
            schedule_info.append(info)
    finally:
        db.close()

    return templates.TemplateResponse("admin/scheduler.html", {
        "request": request,
        "tenants": tenants,
        "health_ok": health_ok,
        "health_data": health_data,
        "schedule_info": schedule_info,
        "slots_meta": slots_meta,
        "default_slot": DEFAULT_SLOT,
        "active_page": "scheduler",
        "active_tenant": None,
    })
