
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.prompts.template_not_found_error import TemplateNotFoundError

__all__ = [
    "TemplateNotFoundError",
    "TemplateRegistry",
    "default_template_dirs",
    "load_partial_templates",
    "packaged_template_root",
]

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


def _default_reader(p: Path) -> str:
    return p.read_text(encoding="utf-8")


_DEFAULT_READER: Callable[[Path], str] = _default_reader


class _PackagedTemplateCache:

    def __init__(
        self,
        *,
        reader: Callable[[Path], str] | None = None,
        partials_loader: Callable[[Path], dict[str, str]] | None = None,
    ) -> None:
        self._reader: Callable[[Path], str] = reader or _DEFAULT_READER
        self._partials_loader: Callable[[Path], dict[str, str]] | None = partials_loader
        self._cache: dict[str, str] = {}
        self._partials: dict[str, dict[str, str]] = {}

    def partials(self, directory: Path) -> dict[str, str]:
        key = str(directory)
        cached = self._partials.get(key)
        if cached is None:
            loader = self._partials_loader or _read_partials_from_directory
            cached = loader(directory)
            self._partials[key] = cached
        return dict(cached)

    def get(self, relative_path: str, *, root: Path) -> str:
        cached = self._cache.get(relative_path)
        if cached is not None:
            return cached
        text = self._reader(root / relative_path)
        self._cache[relative_path] = text
        return text

    def clear(self) -> None:
        self._cache.clear()
        self._partials.clear()


_packaged_template_cache = _PackagedTemplateCache()


class TemplateRegistry:

    def __init__(
        self,
        *,
        template_dirs: tuple[Path, ...] = (),
        _read_text: Callable[[Path], str] | None = None,
    ) -> None:
        self._templates: dict[str, str] = {}
        self._template_dirs = template_dirs
        self._read_text: Callable[[Path], str] = _read_text or _DEFAULT_READER

    def register_template(self, name: str, content: str) -> None:

        self._templates[name] = content

    def get_template(self, name: str) -> str:

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
                    text = self._read_text(path)
                    self._templates[name] = text
                    return text
        return None


def load_partial_templates(template_dirs: Iterable[Path]) -> dict[str, str]:
    packaged_root = packaged_template_root()
    partials: dict[str, str] = {}
    for directory in template_dirs:
        if not directory.exists() or not directory.is_dir():
            continue
        if directory.is_relative_to(packaged_root):
            partials.update(_packaged_template_cache.partials(directory))
            continue
        partials.update(_read_partials_from_directory(directory))
    return partials


def _read_partials_from_directory(directory: Path) -> dict[str, str]:
    partials: dict[str, str] = {}
    for suffix in ("*.jinja", "*.j2", "*.txt"):
        for path in directory.rglob(suffix):
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

    return (
        workspace_root / ".agent" / "prompts" / "shared",
        workspace_root / ".agent" / "prompts",
        workspace_root / ".agent" / "prompts" / "partials",
        packaged_template_root(),
        packaged_template_root() / "shared",
    )
