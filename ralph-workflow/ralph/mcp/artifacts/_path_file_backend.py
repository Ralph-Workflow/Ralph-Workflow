"""Concrete file backend using pathlib.Path operations."""

from __future__ import annotations

import os
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
        with path.open("w", encoding=encoding) as stream:
            stream.write(content)
            stream.flush()
            descriptor = stream.fileno()
            if isinstance(descriptor, int):
                os.fsync(descriptor)

    def read_bytes(self, path: Path) -> bytes:
        return path.read_bytes()

    def write_bytes(self, path: Path, content: bytes) -> None:
        with path.open("wb") as stream:
            stream.write(content)
            stream.flush()
            descriptor = stream.fileno()
            if isinstance(descriptor, int):
                os.fsync(descriptor)

    def replace(self, source: Path, destination: Path) -> None:
        source.replace(destination)

    def sync_directory(self, path: Path) -> None:
        descriptor = os.open(
            path, os.O_RDONLY
        )  # resource-lifecycle-ok: closed in finally after bounded directory sync
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        path.unlink(missing_ok=missing_ok)

    def glob(self, path: Path, pattern: str) -> list[Path]:
        return list(path.glob(pattern))


DEFAULT_FILE_BACKEND = PathFileBackend()
