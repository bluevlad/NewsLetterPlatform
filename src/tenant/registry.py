"""
테넌트 레지스트리 - 등록된 테넌트 관리 + 자동 발견

P1b(2026-08-15): src/tenant/ 하위 패키지를 스캔해 BaseTenant 구현을 자동
등록한다. 새 테넌트 추가 시 main.py 등 플랫폼 파일을 건드릴 필요가 없다 —
`src/tenant/{name}/` 패키지의 __init__ 에 BaseTenant 서브클래스만 정의하면 됨.
"""

import importlib
import logging
import pkgutil
from typing import Dict, List, Optional

from .base import BaseTenant

logger = logging.getLogger(__name__)


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


def discover_and_register() -> List[str]:
    """src/tenant/ 하위 패키지에서 BaseTenant 구현을 자동 발견·등록.

    규약: 각 테넌트 패키지의 __init__ 모듈에 BaseTenant 서브클래스를 정의한다.
    - __init__.py 가 없는 디렉토리(잔존 __pycache__ 등)는 스캔 대상이 아님
    - 서브클래스가 없는 패키지는 조용히 스킵 (경고 로그)
    - 재호출 시 같은 tenant_id 는 새 인스턴스로 덮어씀 (idempotent)

    Returns:
        등록된 tenant_id 목록 (패키지명 알파벳 순)
    """
    package = importlib.import_module(__package__)
    registered: List[str] = []
    for modinfo in sorted(pkgutil.iter_modules(package.__path__),
                          key=lambda m: m.name):
        if not modinfo.ispkg:
            continue
        module = importlib.import_module(f"{__package__}.{modinfo.name}")
        found = False
        for attr in vars(module).values():
            if (
                isinstance(attr, type)
                and issubclass(attr, BaseTenant)
                and attr is not BaseTenant
                and attr.__module__ == module.__name__
            ):
                tenant = attr()
                _registry.register(tenant)
                registered.append(tenant.tenant_id)
                found = True
        if not found:
            logger.warning(
                "테넌트 패키지 %s 에서 BaseTenant 구현을 찾지 못해 스킵",
                modinfo.name,
            )
    return registered
