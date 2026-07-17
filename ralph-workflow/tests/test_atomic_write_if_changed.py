"""Black-box tests for ralph.mcp.artifacts.idempotent_write.atomic_write_text_if_changed.

Satisfies AC-01: a reusable atomic_write_text_if_changed helper is added
to the existing ralph/mcp/artifacts/idempotent_write.py module (no new
module). It reads the destination, returns False without writing tmp or
replacing when the existing bytes equal content, otherwise writes tmp
and replaces returning True, and fails open on OSError. It never creates
parent directories.

The helper is exercised end-to-end through a counting in-memory
FileBackend whose ``replace`` MOVES stored source content to the
destination (the reference backends implement replace as a no-op, which
is fine for write_text_if_changed but would defeat destination-content
assertions for the atomic path). All tests use no real filesystem I/O,
no tmp_path, no patching, and no ``time.sleep``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.mcp.artifacts.idempotent_write import atomic_write_text_if_changed

if TYPE_CHECKING:
    from collections.abc import Dict


class _ReplacingCountingBackend(FileBackend):
    """In-memory FileBackend that records write_text and replace invocations.

    ``replace(source, destination)`` MOVES stored source content to the
    destination (``self._files[destination] = self._files.pop(source)``)
    so atomic-write assertions on destination content are reachable.
    """

    def __init__(self) -> None:
        self._files: Dict[Path, str] = {}
        self.write_text_calls: list[tuple[Path, str]] = []
        self.replace_calls: list[tuple[Path, Path]] = []

    def exists(self, path: Path) -> bool:
        return path in self._files

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del path, parents, exist_ok

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del encoding
        return self._files[path]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        del encoding
        self.write_text_calls.append((path, content))
        self._files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        self.replace_calls.append((source, destination))
        self._files[destination] = self._files.pop(source)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self._files.pop(path, None)
            return
        del self._files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        del path, pattern
        return []


class _RaisingReadBackend(_ReplacingCountingBackend):
    """FileBackend that claims the destination exists but read_text always raises OSError."""

    def exists(self, path: Path) -> bool:
        return True

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del path, encoding
        raise OSError("permission denied")


def test_atomic_write_regression_writes_and_replaces_when_destination_absent() -> None:
    """Fresh destination: returns True; one write_text to tmp_path + one replace; tmp absent."""
    backend = _ReplacingCountingBackend()
    destination = Path("/virtual-ws/checkpoint.json")
    tmp_path = Path("/virtual-ws/checkpoint.json.tmp")

    result = atomic_write_text_if_changed(
        backend,
        destination,
        "fresh",
        tmp_path=tmp_path,
    )

    assert result is True
    assert backend.write_text_calls == [(tmp_path, "fresh")]
    assert backend.replace_calls == [(tmp_path, destination)]
    assert backend._files[destination] == "fresh"
    assert tmp_path not in backend._files


def test_atomic_write_regression_skips_write_and_replace_when_content_identical() -> None:
    """Identical destination content: returns False; zero writes; zero replaces; bytes unchanged."""
    backend = _ReplacingCountingBackend()
    destination = Path("/virtual-ws/checkpoint.json")
    tmp_path = Path("/virtual-ws/checkpoint.json.tmp")
    backend._files[destination] = "alpha"  # seeding is the documented test seam

    result = atomic_write_text_if_changed(
        backend,
        destination,
        "alpha",
        tmp_path=tmp_path,
    )

    assert result is False
    assert backend.write_text_calls == []
    assert backend.replace_calls == []
    assert backend._files[destination] == "alpha"


def test_atomic_write_regression_writes_and_replaces_when_content_changed() -> None:
    """Changed destination content: returns True; one write_text + one replace; new bytes persisted."""
    backend = _ReplacingCountingBackend()
    destination = Path("/virtual-ws/checkpoint.json")
    tmp_path = Path("/virtual-ws/checkpoint.json.tmp")
    backend._files[destination] = "old"

    result = atomic_write_text_if_changed(
        backend,
        destination,
        "new",
        tmp_path=tmp_path,
    )

    assert result is True
    assert backend.write_text_calls == [(tmp_path, "new")]
    assert backend.replace_calls == [(tmp_path, destination)]
    assert backend._files[destination] == "new"
    assert tmp_path not in backend._files


def test_atomic_write_regression_fails_open_when_read_text_raises_oserror() -> None:
    """Unreadable existing destination (OSError on read): fails open to a real write+replace."""
    backend = _RaisingReadBackend()
    destination = Path("/virtual-ws/locked.json")
    tmp_path = Path("/virtual-ws/locked.json.tmp")

    result = atomic_write_text_if_changed(
        backend,
        destination,
        "recovered",
        tmp_path=tmp_path,
    )

    assert result is True
    assert backend.write_text_calls == [(tmp_path, "recovered")]
    assert backend.replace_calls == [(tmp_path, destination)]
    assert backend._files[destination] == "recovered"
