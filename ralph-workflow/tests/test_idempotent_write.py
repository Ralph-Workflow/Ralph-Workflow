"""Black-box tests for ralph.mcp.artifacts.idempotent_write.

The helper is exercised end-to-end through a counting in-memory
FileBackend (no real filesystem I/O, no tmp_path, no patching).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed

if TYPE_CHECKING:
    from collections.abc import Dict


class _CountingBackend(FileBackend):
    """In-memory FileBackend that records write_text invocations."""

    def __init__(self) -> None:
        self._files: Dict[Path, str] = {}
        self.write_text_calls: list[tuple[Path, str]] = []
        self.read_text_calls: int = 0

    def exists(self, path: Path) -> bool:
        return path in self._files

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del path, parents, exist_ok

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del encoding
        self.read_text_calls += 1
        return self._files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        del encoding
        self.write_text_calls.append((path, content))
        self._files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        del source, destination

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self._files.pop(path, None)
            return
        del self._files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        del path, pattern
        return []


class _RaisingBackend(_CountingBackend):
    """FileBackend that claims the path exists but read_text always raises OSError."""

    def exists(self, path: Path) -> bool:
        return True

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del path, encoding
        raise OSError("permission denied")


def test_skips_write_when_content_identical() -> None:
    """Identical existing content: returns False, zero write_text calls, bytes unchanged."""
    backend = _CountingBackend()
    path = Path("/virtual-ws/file.txt")
    backend._files[path] = "alpha"  # seeding is the documented test seam

    result = write_text_if_changed(backend, path, "alpha")

    assert result is False
    assert backend.write_text_calls == []
    assert backend._files[path] == "alpha"  # content check


def test_writes_when_content_changed() -> None:
    """Different content: returns True, exactly one write_text call, stored bytes updated."""
    backend = _CountingBackend()
    path = Path("/virtual-ws/file.txt")
    backend._files[path] = "old"

    result = write_text_if_changed(backend, path, "new")

    assert result is True
    assert len(backend.write_text_calls) == 1
    assert backend.write_text_calls[0] == (path, "new")
    assert backend._files[path] == "new"


def test_writes_when_path_absent() -> None:
    """Missing path: returns True, exactly one write_text call, stored bytes == content."""
    backend = _CountingBackend()
    path = Path("/virtual-ws/new.txt")

    result = write_text_if_changed(backend, path, "fresh")

    assert result is True
    assert len(backend.write_text_calls) == 1
    assert backend.write_text_calls[0] == (path, "fresh")
    assert backend._files[path] == "fresh"


def test_writes_when_read_text_raises_oserror() -> None:
    """Unreadable existing file (read_text raises OSError): fails open to a write."""
    backend = _RaisingBackend()
    path = Path("/virtual-ws/locked.txt")

    result = write_text_if_changed(backend, path, "recovered")

    assert result is True
    assert len(backend.write_text_calls) == 1
    assert backend.write_text_calls[0] == (path, "recovered")
