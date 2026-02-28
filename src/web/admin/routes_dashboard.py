"""
Admin 대시보드 홈
"""

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ...config import settings
from ...common.database.repository import (
    get_session_factory, SubscriberRepository, SendHistoryRepository
)
from ...common.scheduler.health import HEALTH_FILE, check_health
from ...tenant.registry import get_registry
from ..shared import templates
from .auth import get_admin_or_redirect

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_dashboard_data():
    """대시보드 데이터 수집"""
    registry = get_registry()
    tenants = registry.get_all()
    SessionLocal = get_session_factory()
    db = SessionLocal()

    try:
        tenant_stats = []
        all_errors = []
        for tenant in tenants:
            tid = tenant.tenant_id
            sub_count = SubscriberRepository.count_by_tenant(db, tid, active_only=True)
            today = SendHistoryRepository.get_today_stats(db, tid)
            errors = SendHistoryRepository.get_recent_errors(db, tid, limit=5)
            all_errors.extend(errors)

            rate = round(today["success"] / today["total"] * 100, 1) if today["total"] > 0 else 0
            tenant_stats.append({
                "tenant": tenant,
                "subscribers": sub_count,
                "today": today,
                "success_rate": rate,
            })

        all_errors.sort(key=lambda e: e.sent_at, reverse=True)

        # health 정보
        health_ok = check_health()
        health_data = {}
        if HEALTH_FILE.exists():
            try:
                health_data = json.loads(HEALTH_FILE.read_text())
            except Exception:
                pass

        return tenant_stats, all_errors[:10], health_ok, health_data
    finally:
        db.close()


@router.get("/admin", response_class=HTMLResponse)
async def dashboard(request: Request):
    """대시보드 홈"""
    redirect = get_admin_or_redirect(request)
    if redirect:
        return redirect

    registry = get_registry()
    tenants = registry.get_all()
    tenant_stats, recent_errors, health_ok, health_data = _get_dashboard_data()

    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "tenants": tenants,
        "tenant_stats": tenant_stats,
        "recent_errors": recent_errors,
        "health_ok": health_ok,
        "health_data": health_data,
        "active_page": "dashboard",
        "active_tenant": None,
    })


@router.get("/admin/api/health", response_class=HTMLResponse)
async def health_partial(request: Request):
    """HTMX partial: 헬스 상태"""
    redirect = get_admin_or_redirect(request)
    if redirect:
        return redirect

    health_ok = check_health()
    health_data = {}
    if HEALTH_FILE.exists():
        try:
            health_data = json.loads(HEALTH_FILE.read_text())
        except Exception:
            pass

    if health_ok:
        html = '<span class="badge badge-success">Healthy</span>'
    else:
        html = '<span class="badge badge-danger">Unhealthy</span>'

    for key, val in health_data.items():
        html += f' <span class="text-xs text-muted">{key}: {val}</span>'

    return HTMLResponse(html)
