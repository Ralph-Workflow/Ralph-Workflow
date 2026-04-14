"""Simple registry for prompt templates."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class TemplateNotFoundError(Exception):
    """Raised when a requested template is missing."""

    def __init__(self, template_name: str) -> None:
        super().__init__(f"template '{template_name}' not found")
        self.template_name = template_name


class TemplateRegistry:
    """Registry that holds prompt templates by name."""

    def __init__(self, *, template_dirs: tuple[Path, ...] = ()) -> None:
        self._templates: dict[str, str] = {}
        self._template_dirs = template_dirs

    def register_template(self, name: str, content: str) -> None:
        """Register or replace a prompt template."""

        self._templates[name] = content

    def get_template(self, name: str) -> str:
        """Return the template associated with ``name`` or raise if missing."""

        try:
            return self._templates[name]
        except KeyError as exc:
            discovered = self._discover_template(name)
            if discovered is not None:
                return discovered
            raise TemplateNotFoundError(name) from exc

    def _discover_template(self, name: str) -> str | None:
        candidates = (f"{name}.j2", f"{name}.txt")
        for directory in self._template_dirs:
            for candidate in candidates:
                path = directory / candidate
                if path.exists() and path.is_file():
                    return path.read_text(encoding="utf-8")
        return None


def default_template_dirs(workspace_root: Path) -> tuple[Path, ...]:
    """Convention-over-configuration prompt template directories."""

    return (
        workspace_root / ".agent" / "prompts",
        workspace_root / ".agent" / "prompts" / "partials",
    )
