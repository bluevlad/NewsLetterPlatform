"""
Admin 수동 운영 (미리보기, 테스트 발송, 수집/발송 트리거)
"""

import logging
import threading

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse

from ...config import settings
from ...common.database.repository import (
    get_session_factory, CollectedDataRepository, SubscriberRepository
)
from ...common.delivery.gmail_sender import get_sender
from ...common.template.renderer import get_renderer
from ...common.scheduler.jobs import run_collect_job, run_send_job
from ...tenant.registry import get_registry
from ..shared import templates, get_tenant_or_404
from .auth import get_admin_or_redirect

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/admin/{tenant_id}/send", response_class=HTMLResponse)
async def operations_page(request: Request, tenant_id: str):
    """수동 운영 페이지"""
    redirect = get_admin_or_redirect(request)
    if redirect:
        return redirect

    tenant = get_tenant_or_404(tenant_id)
    registry = get_registry()

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        active_count = SubscriberRepository.count_by_tenant(db, tenant_id, active_only=True)
        collected = CollectedDataRepository.get_all_latest_with_time(db, tenant_id)
        has_data = bool(collected)

        data_info = []
        for dtype, (_, collected_at) in collected.items():
            data_info.append({
                "type": dtype,
                "collected_at": collected_at.strftime("%Y-%m-%d %H:%M") if collected_at else "N/A",
            })

        return templates.TemplateResponse("admin/operations.html", {
            "request": request,
            "tenants": registry.get_all(),
            "tenant": tenant,
            "active_count": active_count,
            "has_data": has_data,
            "data_info": data_info,
            "active_page": "operations",
            "active_tenant": tenant_id,
        })
    finally:
        db.close()


@router.get("/admin/{tenant_id}/send/preview", response_class=HTMLResponse)
async def preview_email(request: Request, tenant_id: str):
    """HTMX partial: 이메일 미리보기 HTML"""
    redirect = get_admin_or_redirect(request)
    if redirect:
        return redirect

    tenant = get_tenant_or_404(tenant_id)
    renderer = get_renderer()

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        collected_data = CollectedDataRepository.get_all_latest(db, tenant_id)
        if not collected_data:
            return HTMLResponse(
                '<div class="text-muted text-sm" style="padding:2rem; text-align:center;">'
                'No collected data available for preview.</div>'
            )

        context = tenant.format_report(collected_data)
        html_content = renderer.render(tenant.email_template, context)
        # Replace unsubscribe placeholder for preview
        html_content = html_content.replace("__UNSUBSCRIBE_URL__", "#preview")

        return templates.TemplateResponse("admin/_preview.html", {
            "request": request,
            "html_content": html_content,
            "subject": tenant.generate_subject(),
        })
    except Exception as e:
        logger.error(f"Preview error: {e}")
        return HTMLResponse(
            f'<div style="color: var(--danger-color); padding:1rem;">Preview error: {e}</div>'
        )
    finally:
        db.close()


@router.post("/admin/{tenant_id}/send/test", response_class=HTMLResponse)
async def send_test_email(request: Request, tenant_id: str, email: str = Form(...)):
    """테스트 메일 발송"""
    redirect = get_admin_or_redirect(request)
    if redirect:
        return redirect

    tenant = get_tenant_or_404(tenant_id)
    sender = get_sender()
    renderer = get_renderer()

    if not sender.is_configured:
        return templates.TemplateResponse("admin/_toast.html", {
            "request": request, "level": "error",
            "message": "Gmail SMTP가 설정되지 않았습니다.",
        })

    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        collected_data = CollectedDataRepository.get_all_latest(db, tenant_id)
        if not collected_data:
            return templates.TemplateResponse("admin/_toast.html", {
                "request": request, "level": "error",
                "message": "수집된 데이터가 없습니다. 먼저 수집을 실행해주세요.",
            })

        context = tenant.format_report(collected_data)
        html_content = renderer.render(tenant.email_template, context)
        html_content = html_content.replace("__UNSUBSCRIBE_URL__", "#test")

        subject = f"[TEST] {tenant.generate_subject()}"
        result = sender.send(
            recipient=email.strip(),
            subject=subject,
            html_content=html_content,
            sender_name=tenant.display_name,
        )

        if result.success:
            return templates.TemplateResponse("admin/_toast.html", {
                "request": request, "level": "success",
                "message": f"Test email sent to {email}",
            })
        else:
            return templates.TemplateResponse("admin/_toast.html", {
                "request": request, "level": "error",
                "message": f"Send failed: {result.error_message}",
            })
    except Exception as e:
        logger.error(f"Test send error: {e}")
        return templates.TemplateResponse("admin/_toast.html", {
            "request": request, "level": "error",
            "message": f"Error: {e}",
        })
    finally:
        db.close()


@router.post("/admin/{tenant_id}/send/collect", response_class=HTMLResponse)
async def trigger_collect(request: Request, tenant_id: str):
    """수집 트리거"""
    redirect = get_admin_or_redirect(request)
    if redirect:
        return redirect

    get_tenant_or_404(tenant_id)

    threading.Thread(
        target=run_collect_job,
        args=(tenant_id,),
        daemon=True,
    ).start()

    return templates.TemplateResponse("admin/_toast.html", {
        "request": request, "level": "info",
        "message": f"Data collection started for {tenant_id}",
    })


@router.post("/admin/{tenant_id}/send/trigger", response_class=HTMLResponse)
async def trigger_send(request: Request, tenant_id: str):
    """발송 트리거"""
    redirect = get_admin_or_redirect(request)
    if redirect:
        return redirect

    get_tenant_or_404(tenant_id)

    threading.Thread(
        target=run_send_job,
        args=(tenant_id,),
        daemon=True,
    ).start()

    return templates.TemplateResponse("admin/_toast.html", {
        "request": request, "level": "info",
        "message": f"Newsletter send started for {tenant_id}",
    })
