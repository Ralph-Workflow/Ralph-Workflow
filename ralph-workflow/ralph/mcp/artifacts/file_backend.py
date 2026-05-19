"""Shared file backend abstractions for MCP persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ralph.mcp.artifacts._path_file_backend import DEFAULT_FILE_BACKEND, PathFileBackend

if TYPE_CHECKING:
    from pathlib import Path


class FileBackend(Protocol):
    """Protocol for filesystem I/O required by artifact persistence."""

    def exists(self, path: Path) -> bool: ...
    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None: ...
    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str: ...
    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None: ...
    def replace(self, source: Path, destination: Path) -> None: ...
    def unlink(self, path: Path, *, missing_ok: bool = False) -> None: ...
    def glob(self, path: Path, pattern: str) -> list[Path]: ...


__all__ = ["DEFAULT_FILE_BACKEND", "FileBackend", "PathFileBackend"]
