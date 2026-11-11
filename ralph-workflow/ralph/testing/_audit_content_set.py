from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "htmlcov",
        "node_modules",
        "site-packages",
        "venv",
    }
)

MAX_FILE_BYTES: int = 2 * 1024 * 1024


def _is_skipped(relative: Path) -> bool:
    return any(part in SKIP_DIRS for part in relative.parts)


def read_tree(root: Path) -> dict[str, str]:
    contents: dict[str, str] = {}
    if not root.is_dir():
        return contents
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if _is_skipped(relative):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        contents[relative.as_posix()] = text
    return contents


def read_manifests(root: Path, names: frozenset[str]) -> dict[str, str]:
    manifests: dict[str, str] = {}
    for name in sorted(names):
        path = root / name
        if not path.is_file():
            continue
        try:
            manifests[name] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return manifests
