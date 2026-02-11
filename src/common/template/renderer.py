"""
Jinja2 템플릿 렌더링 모듈
"""

import logging
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ...config import settings

logger = logging.getLogger(__name__)


class TemplateRenderer:
    """이메일 템플릿 렌더러"""

    def __init__(self, template_dir: str = None):
        if template_dir is None:
            template_dir = settings.BASE_DIR / "templates"

        self.template_dir = Path(template_dir)

        self._env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

        self._env.filters["format_date"] = self._format_date
        self._env.filters["truncate_text"] = self._truncate_text
        self._env.filters["format_number"] = self._format_number
        self._env.filters["format_percent"] = self._format_percent

    @staticmethod
    def _format_date(dt: datetime, fmt: str = "%Y-%m-%d") -> str:
        if dt is None:
            return ""
        if isinstance(dt, str):
            return dt
        return dt.strftime(fmt)

    @staticmethod
    def _truncate_text(text: str, length: int = 150) -> str:
        if not text:
            return ""
        if len(text) <= length:
            return text
        return text[:length] + "..."

    @staticmethod
    def _format_number(value, default: str = "0") -> str:
        if value is None:
            return default
        try:
            return f"{int(value):,}"
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _format_percent(value, decimals: int = 1) -> str:
        if value is None:
            return "0%"
        try:
            return f"{float(value):.{decimals}f}%"
        except (ValueError, TypeError):
            return "0%"

    def render(self, template_name: str, context: dict) -> str:
        """범용 템플릿 렌더링"""
        try:
            template = self._env.get_template(template_name)
            return template.render(**context)
        except Exception as e:
            logger.error(f"템플릿 렌더링 실패 ({template_name}): {e}")
            raise

    def render_verification_email(
        self,
        tenant_name: str,
        email: str,
        name: str,
        code: str,
        verification_type: str = "subscribe"
    ) -> str:
        """인증 이메일 렌더링"""
        if verification_type == "unsubscribe":
            action_text = "구독 해지"
        else:
            action_text = "구독 신청"

        return self.render("verification_code.html", {
            "tenant_name": tenant_name,
            "email": email,
            "name": name,
            "code": code,
            "action_text": action_text,
            "verification_type": verification_type,
        })


_renderer = None


def get_renderer() -> TemplateRenderer:
    global _renderer
    if _renderer is None:
        _renderer = TemplateRenderer()
    return _renderer
