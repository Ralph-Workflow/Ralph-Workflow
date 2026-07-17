"""Black-box tests for sync_markdown_handoff idempotent-write guard.

Satisfies AC-02: sync_markdown_handoff skips the physical write when
the destination already contains byte-identical rendered markdown,
while still guaranteeing the file contains render_markdown_handoff
output; the first write and any changed re-render still write.

The handoff writer is exercised end-to-end through a counting
in-memory FileBackend so no real filesystem I/O is performed (no
``tmp_path``, no ``Path.read_text``/``Path.write_text``, no patching).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.mcp.artifacts.handoffs import (
    render_markdown_handoff,
    sync_markdown_handoff,
)

if TYPE_CHECKING:
    from collections.abc import Dict, Mapping


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


def test_markdown_handoff_regression_skips_write_when_content_identical() -> None:
    """Identical re-render: zero additional write_text; stored content matches the renderer output."""
    backend = _CountingBackend()
    workspace_root = Path("/virtual-ws")
    content: Mapping[str, object] = {
        "summary": "s",
        "status": "request_changes",
        "issues": [],
    }
    destination = workspace_root / ".agent" / "ISSUES.md"
    expected = render_markdown_handoff("issues", content)

    first = sync_markdown_handoff(workspace_root, "issues", content, backend=backend)
    second = sync_markdown_handoff(workspace_root, "issues", content, backend=backend)

    assert first == ".agent/ISSUES.md"
    assert second == ".agent/ISSUES.md"
    # Exactly one write_text total \u2014 the second identical call is a skip.
    assert len(backend.write_text_calls) == 1
    assert backend.write_text_calls[0] == (destination, expected)
    assert backend._files[destination] == expected


def test_markdown_handoff_regression_writes_when_content_changed() -> None:
    """Changed content: a second write_text fires; final stored content matches the new renderer output."""
    backend = _CountingBackend()
    workspace_root = Path("/virtual-ws")
    initial: Mapping[str, object] = {
        "summary": "first",
        "status": "request_changes",
        "issues": [],
    }
    changed: Mapping[str, object] = {
        "summary": "second",
        "status": "request_changes",
        "issues": [],
    }
    destination = workspace_root / ".agent" / "ISSUES.md"
    expected = render_markdown_handoff("issues", changed)

    sync_markdown_handoff(workspace_root, "issues", initial, backend=backend)
    sync_markdown_handoff(workspace_root, "issues", changed, backend=backend)

    assert len(backend.write_text_calls) == 2
    assert backend.write_text_calls[1] == (destination, expected)
    assert backend._files[destination] == expected
