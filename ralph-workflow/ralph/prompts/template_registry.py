"""Simple registry for prompt templates."""

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
    """Default reader: ``Path.read_text`` with utf-8 encoding."""
    return p.read_text(encoding="utf-8")


_DEFAULT_READER: Callable[[Path], str] = _default_reader


class _PackagedTemplateCache:
    """Clearable memoizing loader for the static packaged prompt templates.

    The packaged .jinja templates under ``packaged_template_root()``
    are immutable for the process lifetime. Reading them on every
    call (the pre-wt-024 behavior) wasted I/O. The cache holds a
    dict ``relative_path -> text`` and serves repeat calls from memory.

    ``reader`` is an injectable ``Callable[[Path], str]`` (default:
    ``Path.read_text``) so tests can count invocations without real
    disk I/O. ``clear()`` resets the cache for test isolation.
    """

    def __init__(
        self,
        *,
        reader: Callable[[Path], str] | None = None,
        partials_loader: Callable[[Path], dict[str, str]] | None = None,
    ) -> None:
        self._reader: Callable[[Path], str] = reader or _DEFAULT_READER
        # Resolved lazily in ``partials``: the module-level cache instance is
        # constructed above the default loader's definition.
        self._partials_loader: Callable[[Path], dict[str, str]] | None = partials_loader
        # bounded-accumulator-ok: bounded by immutable packaged-template file set
        self._cache: dict[str, str] = {}  # bounded-accumulator-ok: packaged templates
        # bounded-accumulator-ok: one entry per packaged template directory
        self._partials: dict[str, dict[str, str]] = {}  # bounded-accumulator-ok: packaged dirs

    def partials(self, directory: Path) -> dict[str, str]:
        """Return the packaged partial set for ``directory``, reading it once.

        Snapshotting the packaged tree on first use keeps the templates a
        process renders consistent with the variable set its code supplies.
        Without it, a checkout that moves under a running pipeline feeds
        newly-added template text to older variable code mid-run.
        """
        key = str(directory)
        cached = self._partials.get(key)
        if cached is None:
            loader = self._partials_loader or _read_partials_from_directory
            cached = loader(directory)
            self._partials[key] = cached
        return dict(cached)

    def get(self, relative_path: str, *, root: Path) -> str:
        """Return the packaged template body for ``relative_path``.

        ``root`` is the packaged templates directory (typically
        ``packaged_template_root()``); ``relative_path`` is the
        template path under ``root``. The cache key is the relative
        path so the same name resolves to the same text across
        callers.
        """
        cached = self._cache.get(relative_path)
        if cached is not None:
            return cached
        text = self._reader(root / relative_path)
        self._cache[relative_path] = text
        return text

    def clear(self) -> None:
        """Drop every cached template body. Used by tests for isolation."""
        self._cache.clear()
        self._partials.clear()


_packaged_template_cache = _PackagedTemplateCache()
"""Process-wide cache for packaged templates; mutable for tests via ``clear()``."""


class TemplateRegistry:
    """Registry that holds prompt templates by name."""

    def __init__(
        self,
        *,
        template_dirs: tuple[Path, ...] = (),
        _read_text: Callable[[Path], str] | None = None,
    ) -> None:
        # bounded-accumulator-ok: bounded by template_dirs file set
        # (packaged + workspace), lazily discovered via _discover_template;
        # register_template has zero production callers
        self._templates: dict[str, str] = {}  # bounded-accumulator-ok: template_dirs
        self._template_dirs = template_dirs
        self._read_text: Callable[[Path], str] = _read_text or _DEFAULT_READER

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
                    text = self._read_text(path)
                    # Backfill the in-memory cache so subsequent
                    # get_template calls for this name are served from
                    # memory without re-reading from disk (wt-024 P3).
                    self._templates[name] = text
                    return text
        return None


def load_partial_templates(template_dirs: Iterable[Path]) -> dict[str, str]:
    """Load all Jinja/j2/txt templates from the given directories into a dict.

    Directories under :func:`packaged_template_root` are served from the
    process-wide snapshot so every render in a process sees the same
    packaged template text; workspace override directories are re-read on
    each call because they are expected to change while the pipeline runs.
    """
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
    """Read every Jinja/j2/txt template directly under ``directory``."""
    partials: dict[str, str] = {}
    for suffix in ("*.jinja", "*.j2", "*.txt"):
        for path in directory.rglob(suffix):
            key = _relative_template_key(directory, path)
            partials[key] = path.read_text(encoding="utf-8")
    return partials


def packaged_template_root() -> Path:
    """Return the path to the bundled prompt templates directory."""
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
