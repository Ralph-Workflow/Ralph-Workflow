"""Simple registry for prompt templates."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


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
        candidates = _template_candidates(name)
        for directory in self._template_dirs:
            for candidate in candidates:
                path = directory / candidate
                if path.exists() and path.is_file():
                    return path.read_text(encoding="utf-8")
        return None


def load_partial_templates(template_dirs: Iterable[Path]) -> dict[str, str]:
    partials: dict[str, str] = {}
    for directory in template_dirs:
        if not directory.exists() or not directory.is_dir():
            continue
        for path in directory.rglob("*.jinja"):
            key = _relative_template_key(directory, path)
            partials[key] = path.read_text(encoding="utf-8")
        for path in directory.rglob("*.j2"):
            key = _relative_template_key(directory, path)
            partials[key] = path.read_text(encoding="utf-8")
        for path in directory.rglob("*.txt"):
            key = _relative_template_key(directory, path)
            partials[key] = path.read_text(encoding="utf-8")
    return partials


def packaged_template_root() -> Path:
    return Path(__file__).resolve().parent / "templates"


def _template_candidates(name: str) -> tuple[str, ...]:
    path = Path(name)
    if path.suffix:
        return (name,)
    return (f"{name}.jinja", f"{name}.j2", f"{name}.txt")


def _relative_template_key(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    without_suffix = relative.with_suffix("")
    return without_suffix.as_posix()


def default_template_dirs(workspace_root: Path) -> tuple[Path, ...]:
    """Convention-over-configuration prompt template directories."""

    return (
        workspace_root / ".agent" / "prompts" / "shared",
        workspace_root / ".agent" / "prompts",
        workspace_root / ".agent" / "prompts" / "partials",
        packaged_template_root(),
        packaged_template_root() / "shared",
    )
