"""Concrete file backend using pathlib.Path operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class PathFileBackend:
    """Concrete FileBackend implementation backed by pathlib.Path operations."""

    def exists(self, path: Path) -> bool:
        return path.exists()

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        path.mkdir(parents=parents, exist_ok=exist_ok)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        return path.read_text(encoding=encoding)

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        path.write_text(content, encoding=encoding)

    def replace(self, source: Path, destination: Path) -> None:
        source.replace(destination)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        path.unlink(missing_ok=missing_ok)

    def glob(self, path: Path, pattern: str) -> list[Path]:
        return list(path.glob(pattern))


DEFAULT_FILE_BACKEND = PathFileBackend()
