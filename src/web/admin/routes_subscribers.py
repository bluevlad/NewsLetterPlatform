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
from ...common.scheduler.slots import (
    DAILY_SLOTS, DEFAULT_SLOT, SLOT_KEYS, normalize_slot, get_slots_for_template,
)
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
        total_all = active_count + inactive_count

        slot_counts = SubscriberRepository.count_by_slot(db, tenant_id)
        slots_meta = get_slots_for_template()

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
            "slots_meta": slots_meta,
            "slot_counts": slot_counts,
            "default_slot": DEFAULT_SLOT,
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
            "slots_meta": get_slots_for_template(),
            "default_slot": DEFAULT_SLOT,
        })
    finally:
        db.close()


@router.post("/admin/{tenant_id}/subscribers/{subscriber_id}/slot", response_class=HTMLResponse)
async def subscriber_change_slot(
    request: Request, tenant_id: str, subscriber_id: int,
    slot: str = Form(...),
):
    """개별 구독자 슬롯 변경 (HTMX)"""
    redirect = get_admin_or_redirect(request)
    if redirect:
        return redirect

    get_tenant_or_404(tenant_id)

    if slot not in SLOT_KEYS:
        return templates.TemplateResponse("admin/_toast.html", {
            "request": request, "level": "error",
            "message": f"유효하지 않은 슬롯: {slot}",
        })

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        subscriber = SubscriberRepository.get_by_id(db, subscriber_id)
        if not subscriber or subscriber.tenant_id != tenant_id:
            return templates.TemplateResponse("admin/_toast.html", {
                "request": request, "level": "error",
                "message": "구독자를 찾을 수 없습니다.",
            })

        SubscriberRepository.update_slot(db, subscriber_id, slot)
        db.commit()

        return templates.TemplateResponse("admin/_toast.html", {
            "request": request, "level": "success",
            "message": f"{subscriber.email} 발송 시간이 변경되었습니다.",
        })
    except Exception as e:
        db.rollback()
        logger.error(f"슬롯 변경 오류: {e}")
        return templates.TemplateResponse("admin/_toast.html", {
            "request": request, "level": "error",
            "message": f"오류가 발생했습니다: {e}",
        })
    finally:
        db.close()


@router.post("/admin/{tenant_id}/subscribers/bulk_slot", response_class=HTMLResponse)
async def subscribers_bulk_slot(
    request: Request, tenant_id: str,
    slot: str = Form(...),
):
    """선택된 구독자들의 슬롯 일괄 변경 (HTMX)

    Form 필드 'subscriber_ids'(반복) 으로 전달된 ID 목록을 사용한다.
    """
    redirect = get_admin_or_redirect(request)
    if redirect:
        return redirect

    get_tenant_or_404(tenant_id)

    if slot not in SLOT_KEYS:
        return templates.TemplateResponse("admin/_toast.html", {
            "request": request, "level": "error",
            "message": f"유효하지 않은 슬롯: {slot}",
        })

    form = await request.form()
    raw_ids = form.getlist("subscriber_ids")
    try:
        subscriber_ids = [int(x) for x in raw_ids]
    except ValueError:
        subscriber_ids = []

    if not subscriber_ids:
        return templates.TemplateResponse("admin/_toast.html", {
            "request": request, "level": "error",
            "message": "선택된 구독자가 없습니다.",
        })

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        updated = SubscriberRepository.bulk_update_slot(db, tenant_id, subscriber_ids, slot)
        db.commit()

        slot_label = next((s["label"] for s in DAILY_SLOTS if s["key"] == slot), slot)
        return templates.TemplateResponse("admin/_toast.html", {
            "request": request, "level": "success",
            "message": f"{updated}명의 발송 시간을 '{slot_label}'(으)로 일괄 변경했습니다.",
        })
    except Exception as e:
        db.rollback()
        logger.error(f"슬롯 일괄 변경 오류: {e}")
        return templates.TemplateResponse("admin/_toast.html", {
            "request": request, "level": "error",
            "message": f"오류가 발생했습니다: {e}",
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


@router.post("/admin/{tenant_id}/subscribers/{subscriber_id}/delete", response_class=HTMLResponse)
async def subscriber_delete(request: Request, tenant_id: str, subscriber_id: int):
    """구독자 영구 삭제 (HTMX)"""
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

        email = subscriber.email
        SubscriberRepository.delete(db, subscriber_id)
        db.commit()

        return templates.TemplateResponse("admin/_toast.html", {
            "request": request, "level": "success",
            "message": f"{email} 구독자가 삭제되었습니다.",
        })
    except Exception as e:
        db.rollback()
        logger.error(f"구독자 삭제 오류: {e}")
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
        writer.writerow(["email", "name", "is_active", "send_slot", "created_at"])
        for s in subscribers:
            writer.writerow([
                s.email, s.name or "", s.is_active,
                normalize_slot(s.send_slot),
                s.created_at.isoformat(),
            ])

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={tenant_id}_subscribers.csv"},
        )
    finally:
        db.close()
