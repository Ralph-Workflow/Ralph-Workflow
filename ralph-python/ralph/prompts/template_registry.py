"""Simple registry for prompt templates."""

from __future__ import annotations


class TemplateNotFound(Exception):
    """Raised when a requested template is missing."""

    def __init__(self, template_name: str) -> None:
        super().__init__(f"template '{template_name}' not found")
        self.template_name = template_name


class TemplateRegistry:
    """Registry that holds prompt templates by name."""

    def __init__(self) -> None:
        self._templates: dict[str, str] = {}

    def register_template(self, name: str, content: str) -> None:
        """Register or replace a prompt template."""

        self._templates[name] = content

    def get_template(self, name: str) -> str:
        """Return the template associated with ``name`` or raise if missing."""

        try:
            return self._templates[name]
        except KeyError as exc:
            raise TemplateNotFound(name) from exc
