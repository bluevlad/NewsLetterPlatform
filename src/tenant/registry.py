"""
테넌트 레지스트리 - 등록된 테넌트 관리
"""

from typing import Dict, List, Optional

from .base import BaseTenant


class TenantRegistry:
    """테넌트 레지스트리 싱글턴"""

    _instance = None
    _tenants: Dict[str, BaseTenant] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tenants = {}
        return cls._instance

    def register(self, tenant: BaseTenant) -> None:
        """테넌트 등록"""
        self._tenants[tenant.tenant_id] = tenant

    def get(self, tenant_id: str) -> Optional[BaseTenant]:
        """테넌트 조회"""
        return self._tenants.get(tenant_id)

    def get_all(self) -> List[BaseTenant]:
        """모든 테넌트 조회"""
        return list(self._tenants.values())

    def get_active_ids(self) -> List[str]:
        """활성 테넌트 ID 목록"""
        return list(self._tenants.keys())


_registry = TenantRegistry()


def get_registry() -> TenantRegistry:
    return _registry
