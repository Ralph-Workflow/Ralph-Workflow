"""Black-box tests for the FsWorkspace idempotent write guard.

Exercises ``FsWorkspace.write`` end-to-end through a counting
in-memory FileBackend so no real filesystem I/O is performed
(no ``tmp_path``, no ``Path.read_text``/``Path.write_text``,
no patching of ``pathlib``).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Dict


class _CountingBackend(FileBackend):
    """In-memory FileBackend that records write_text invocations."""

    def __init__(self) -> None:
        self._files: Dict[Path, str] = {}
        self.write_text_calls: list[tuple[Path, str]] = []
        self.mkdir_calls: list[Path] = []

    def exists(self, path: Path) -> bool:
        return path in self._files

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del parents, exist_ok
        self.mkdir_calls.append(path)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del encoding
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


def test_fresh_write_creates_file_with_expected_content() -> None:
    """First write to a fresh path: exactly one write_text call, stored bytes match."""
    backend = _CountingBackend()
    ws = FsWorkspace(Path("/virtual-ws"), backend=backend)

    ws.write("output.txt", "alpha")

    assert len(backend.write_text_calls) == 1
    assert backend.write_text_calls[0][1] == "alpha"  # content check
    assert backend._files[Path("/virtual-ws/output.txt")] == "alpha"


def test_identical_write_is_skipped() -> None:
    """Second write with identical content: zero additional write_text calls, bytes unchanged."""
    backend = _CountingBackend()
    ws = FsWorkspace(Path("/virtual-ws"), backend=backend)

    ws.write("output.txt", "alpha")
    ws.write("output.txt", "alpha")

    # Two ``ws.write`` calls but the second is a skip — only one real write.
    assert len(backend.write_text_calls) == 1
    assert backend.write_text_calls[0][1] == "alpha"  # content check
    assert backend._files[Path("/virtual-ws/output.txt")] == "alpha"  # unchanged


def test_changed_write_persists() -> None:
    """Write a different content: second write_text call, stored bytes reflect new content."""
    backend = _CountingBackend()
    ws = FsWorkspace(Path("/virtual-ws"), backend=backend)

    ws.write("output.txt", "alpha")
    ws.write("output.txt", "beta")

    assert len(backend.write_text_calls) == 2
    assert backend.write_text_calls[1][1] == "beta"  # content check
    assert backend._files[Path("/virtual-ws/output.txt")] == "beta"


def test_mkdir_is_routed_through_injected_backend() -> None:
    """The parent-directory mkdir is intercepted by the injected backend (no real I/O)."""
    backend = _CountingBackend()
    ws = FsWorkspace(Path("/virtual-ws"), backend=backend)

    ws.write("a/b/c/deep.txt", "content")

    # Parent directory mkdir goes through the backend, not the real filesystem.
    assert any(path == Path("/virtual-ws/a/b/c") for path in backend.mkdir_calls)
    assert backend._files[Path("/virtual-ws/a/b/c/deep.txt")] == "content"


def test_write_signature_returns_none() -> None:
    """Public write() signature unchanged: returns None, accepts (str, str)."""
    backend = _CountingBackend()
    ws = FsWorkspace(Path("/virtual-ws"), backend=backend)

    result = ws.write("file.txt", "data")

    assert result is None
