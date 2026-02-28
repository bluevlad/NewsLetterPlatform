"""
Admin 스케줄러 모니터링
"""

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ...common.scheduler.health import HEALTH_FILE, check_health
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

    # Build schedule info per tenant
    schedule_info = []
    for tenant in tenants:
        config = tenant.schedule_config
        schedule_info.append({
            "tenant": tenant,
            "collect_time": f"{config['collect_hour']:02d}:{config['collect_minute']:02d}",
            "send_time": f"{config['send_hour']:02d}:{config['send_minute']:02d}",
            "last_collect": health_data.get("collect", "N/A"),
            "last_send": health_data.get("send", "N/A"),
        })

    return templates.TemplateResponse("admin/scheduler.html", {
        "request": request,
        "tenants": tenants,
        "health_ok": health_ok,
        "health_data": health_data,
        "schedule_info": schedule_info,
        "active_page": "scheduler",
        "active_tenant": None,
    })
