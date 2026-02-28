"""
Admin 발송 이력
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ...common.database.repository import (
    get_session_factory, SendHistoryRepository, SubscriberRepository
)
from ...tenant.registry import get_registry
from ..shared import templates, get_tenant_or_404
from .auth import get_admin_or_redirect

logger = logging.getLogger(__name__)

router = APIRouter()


def _parse_date(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None


def _build_history_context(request, tenant, tenant_id, db):
    """공통 이력 데이터 빌드"""
    date_from_str = request.query_params.get("date_from", "")
    date_to_str = request.query_params.get("date_to", "")
    status_filter = request.query_params.get("status", "all")
    page = int(request.query_params.get("page", "1"))
    per_page = 20
    offset = (page - 1) * per_page

    date_from = _parse_date(date_from_str)
    date_to = _parse_date(date_to_str)

    success_only = None
    if status_filter == "success":
        success_only = True
    elif status_filter == "failed":
        success_only = False

    history, total = SendHistoryRepository.get_history_paginated(
        db, tenant_id, date_from=date_from, date_to=date_to,
        success_only=success_only, offset=offset, limit=per_page,
    )
    total_pages = max(1, (total + per_page - 1) // per_page)

    # subscriber email lookup
    subscriber_ids = {h.subscriber_id for h in history}
    subscriber_map = {}
    for sid in subscriber_ids:
        sub = SubscriberRepository.get_by_id(db, sid)
        if sub:
            subscriber_map[sid] = sub.email

    daily_summary = SendHistoryRepository.get_daily_summary(db, tenant_id, days=7)

    return {
        "tenant": tenant,
        "history": history,
        "subscriber_map": subscriber_map,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "date_from": date_from_str,
        "date_to": date_to_str,
        "status_filter": status_filter,
        "daily_summary": daily_summary,
    }


@router.get("/admin/{tenant_id}/history", response_class=HTMLResponse)
async def history_page(request: Request, tenant_id: str):
    """발송 이력 페이지"""
    redirect = get_admin_or_redirect(request)
    if redirect:
        return redirect

    tenant = get_tenant_or_404(tenant_id)
    registry = get_registry()

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        ctx = _build_history_context(request, tenant, tenant_id, db)
        ctx.update({
            "request": request,
            "tenants": registry.get_all(),
            "active_page": "history",
            "active_tenant": tenant_id,
        })
        return templates.TemplateResponse("admin/history.html", ctx)
    finally:
        db.close()


@router.get("/admin/{tenant_id}/history/filter", response_class=HTMLResponse)
async def history_filter(request: Request, tenant_id: str):
    """HTMX partial: 날짜/상태 필터 결과"""
    redirect = get_admin_or_redirect(request)
    if redirect:
        return redirect

    tenant = get_tenant_or_404(tenant_id)

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        ctx = _build_history_context(request, tenant, tenant_id, db)
        ctx["request"] = request
        return templates.TemplateResponse("admin/_history_rows.html", ctx)
    finally:
        db.close()
