"""
Admin 라우터 조립
"""

from fastapi import APIRouter

from .auth import router as auth_router
from .routes_dashboard import router as dashboard_router
from .routes_subscribers import router as subscribers_router
from .routes_history import router as history_router
from .routes_operations import router as operations_router
from .routes_scheduler import router as scheduler_router

admin_router = APIRouter()

# auth (로그인/로그아웃)은 인증 불필요
admin_router.include_router(auth_router)

# 인증 필요한 라우트들
admin_router.include_router(dashboard_router)
admin_router.include_router(subscribers_router)
admin_router.include_router(history_router)
admin_router.include_router(operations_router)
admin_router.include_router(scheduler_router)
