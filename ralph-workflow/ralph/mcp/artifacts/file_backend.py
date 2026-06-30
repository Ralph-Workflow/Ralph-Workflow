"""Shared file backend abstractions for MCP persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ralph.mcp.artifacts._path_file_backend import DEFAULT_FILE_BACKEND, PathFileBackend

if TYPE_CHECKING:
    from pathlib import Path


class FileBackend(Protocol):
    """Protocol for filesystem I/O required by artifact persistence."""

    def exists(self, path: Path) -> bool:
        """Return True if `path` currently exists on the backend."""
        ...

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        """Create the directory at `path`.

        Optionally create parents and tolerate an existing directory.
        """
        ...

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        """Read and return the textual contents of `path` decoded with `encoding`."""
        ...

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        """Write `content` to `path` using `encoding`, replacing any existing file."""
        ...

    def replace(self, source: Path, destination: Path) -> None:
        """Atomically move `source` to `destination`, replacing any existing file."""
        ...

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        """Remove the file at `path`; if `missing_ok`, do not raise when the file is absent."""
        ...

    def glob(self, path: Path, pattern: str) -> list[Path]:
        """Return all paths under `path` matching the glob `pattern`."""
        ...


__all__ = ["DEFAULT_FILE_BACKEND", "FileBackend", "PathFileBackend"]
