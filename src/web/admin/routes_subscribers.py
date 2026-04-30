"""
Admin 구독자 관리
"""

import csv
import io
import logging
import secrets

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse

from ...common.database.repository import get_session_factory, SubscriberRepository
from ...tenant.registry import get_registry
from ..shared import templates, get_tenant_or_404
from .auth import get_admin_or_redirect

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/admin/{tenant_id}/subscribers", response_class=HTMLResponse)
async def subscribers_page(request: Request, tenant_id: str):
    """구독자 관리 페이지"""
    redirect = get_admin_or_redirect(request)
    if redirect:
        return redirect

    tenant = get_tenant_or_404(tenant_id)
    registry = get_registry()

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        search = request.query_params.get("search", "")
        status_filter = request.query_params.get("status", "all")
        page = int(request.query_params.get("page", "1"))
        per_page = 20
        offset = (page - 1) * per_page

        active_only = None
        if status_filter == "active":
            active_only = True
        elif status_filter == "inactive":
            active_only = False

        subscribers, total = SubscriberRepository.get_all_by_tenant(
            db, tenant_id, active_only=active_only,
            search=search, offset=offset, limit=per_page,
        )
        total_pages = max(1, (total + per_page - 1) // per_page)

        active_count = SubscriberRepository.count_by_tenant(db, tenant_id, active_only=True)
        inactive_count = SubscriberRepository.count_by_tenant(db, tenant_id, active_only=False) - active_count
        # inactive = total_all - active
        total_all = active_count + inactive_count

        return templates.TemplateResponse("admin/subscribers.html", {
            "request": request,
            "tenants": registry.get_all(),
            "tenant": tenant,
            "subscribers": subscribers,
            "total": total,
            "total_all": total_all,
            "active_count": active_count,
            "inactive_count": inactive_count,
            "page": page,
            "total_pages": total_pages,
            "search": search,
            "status_filter": status_filter,
            "active_page": "subscribers",
            "active_tenant": tenant_id,
        })
    finally:
        db.close()


@router.get("/admin/{tenant_id}/subscribers/search", response_class=HTMLResponse)
async def subscribers_search(request: Request, tenant_id: str):
    """HTMX partial: 구독자 검색/필터 결과"""
    redirect = get_admin_or_redirect(request)
    if redirect:
        return redirect

    tenant = get_tenant_or_404(tenant_id)

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        search = request.query_params.get("search", "")
        status_filter = request.query_params.get("status", "all")
        page = int(request.query_params.get("page", "1"))
        per_page = 20
        offset = (page - 1) * per_page

        active_only = None
        if status_filter == "active":
            active_only = True
        elif status_filter == "inactive":
            active_only = False

        subscribers, total = SubscriberRepository.get_all_by_tenant(
            db, tenant_id, active_only=active_only,
            search=search, offset=offset, limit=per_page,
        )
        total_pages = max(1, (total + per_page - 1) // per_page)

        return templates.TemplateResponse("admin/_subscriber_rows.html", {
            "request": request,
            "tenant": tenant,
            "subscribers": subscribers,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "search": search,
            "status_filter": status_filter,
        })
    finally:
        db.close()


@router.post("/admin/{tenant_id}/subscribers/add", response_class=HTMLResponse)
async def subscriber_add(
    request: Request, tenant_id: str,
    email: str = Form(...), name: str = Form(default=""),
):
    """구독자 추가 (HTMX)"""
    redirect = get_admin_or_redirect(request)
    if redirect:
        return redirect

    get_tenant_or_404(tenant_id)

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        existing = SubscriberRepository.get_by_email(db, tenant_id, email.strip().lower())
        if existing:
            if not existing.is_active:
                existing.is_active = True
                db.commit()
                return templates.TemplateResponse("admin/_toast.html", {
                    "request": request, "level": "success",
                    "message": f"{email} 구독자가 다시 활성화되었습니다.",
                })
            return templates.TemplateResponse("admin/_toast.html", {
                "request": request, "level": "error",
                "message": f"{email} 은(는) 이미 등록된 구독자입니다.",
            })

        token = secrets.token_urlsafe(32)
        SubscriberRepository.create(db, tenant_id, email.strip().lower(), name.strip(), token)
        db.commit()

        return templates.TemplateResponse("admin/_toast.html", {
            "request": request, "level": "success",
            "message": f"{email} 구독자가 추가되었습니다.",
        })
    except Exception as e:
        db.rollback()
        logger.error(f"구독자 추가 오류: {e}")
        return templates.TemplateResponse("admin/_toast.html", {
            "request": request, "level": "error",
            "message": f"오류가 발생했습니다: {e}",
        })
    finally:
        db.close()


@router.post("/admin/{tenant_id}/subscribers/{subscriber_id}/toggle", response_class=HTMLResponse)
async def subscriber_toggle(request: Request, tenant_id: str, subscriber_id: int):
    """구독자 활성/비활성 토글 (HTMX)"""
    redirect = get_admin_or_redirect(request)
    if redirect:
        return redirect

    get_tenant_or_404(tenant_id)

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        subscriber = SubscriberRepository.get_by_id(db, subscriber_id)
        if not subscriber or subscriber.tenant_id != tenant_id:
            return templates.TemplateResponse("admin/_toast.html", {
                "request": request, "level": "error",
                "message": "구독자를 찾을 수 없습니다.",
            })

        subscriber.is_active = not subscriber.is_active
        db.commit()

        status = "활성화" if subscriber.is_active else "비활성화"
        return templates.TemplateResponse("admin/_toast.html", {
            "request": request, "level": "success",
            "message": f"{subscriber.email} {status}되었습니다.",
        })
    except Exception as e:
        db.rollback()
        logger.error(f"구독자 토글 오류: {e}")
        return templates.TemplateResponse("admin/_toast.html", {
            "request": request, "level": "error",
            "message": f"오류가 발생했습니다: {e}",
        })
    finally:
        db.close()


@router.get("/admin/{tenant_id}/subscribers/export")
async def subscribers_export(request: Request, tenant_id: str):
    """구독자 CSV 내보내기"""
    redirect = get_admin_or_redirect(request)
    if redirect:
        return redirect

    get_tenant_or_404(tenant_id)

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        subscribers, _ = SubscriberRepository.get_all_by_tenant(
            db, tenant_id, active_only=None, offset=0, limit=100000,
        )

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["email", "name", "is_active", "created_at"])
        for s in subscribers:
            writer.writerow([s.email, s.name or "", s.is_active, s.created_at.isoformat()])

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={tenant_id}_subscribers.csv"},
        )
    finally:
        db.close()
