"""
공유 객체 모듈 - 순환 import 방지
app.py와 admin 라우트 모두에서 사용하는 공통 객체
"""

from pathlib import Path

from fastapi import HTTPException
from fastapi.templating import Jinja2Templates

from ..common.database.repository import get_session_factory
from ..tenant.registry import get_registry

# 웹 페이지 템플릿
templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))


def get_db():
    """데이터베이스 세션 제너레이터"""
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_tenant_or_404(tenant_id: str):
    """테넌트 조회, 없으면 404"""
    if not tenant_id or len(tenant_id) > 64 or not tenant_id.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="잘못된 테넌트 ID 형식입니다")
    registry = get_registry()
    tenant = registry.get(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail=f"테넌트를 찾을 수 없습니다: {tenant_id}")
    return tenant
