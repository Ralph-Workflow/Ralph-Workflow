
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ralph.mcp.artifacts._path_file_backend import DEFAULT_FILE_BACKEND, PathFileBackend

if TYPE_CHECKING:
    from pathlib import Path


class FileBackend(Protocol):

    def exists(self, path: Path) -> bool:
        ...

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        ...

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        ...

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        ...

    def read_bytes(self, path: Path) -> bytes:
        ...

    def write_bytes(self, path: Path, content: bytes) -> None:
        ...

    def replace(self, source: Path, destination: Path) -> None:
        ...

    def sync_directory(self, path: Path) -> None:
        ...

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        ...

    def glob(self, path: Path, pattern: str) -> list[Path]:
        ...


__all__ = ["DEFAULT_FILE_BACKEND", "FileBackend", "PathFileBackend"]
