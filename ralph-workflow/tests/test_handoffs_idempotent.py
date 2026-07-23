"""Black-box tests for the parallel-summary handoff writer's idempotent-write guard.

The parallel_development_summary is an internally generated record (not an
agent-authored markdown artifact), so fan-out renders its own markdown handoff
directly. The writer must skip the physical write when the destination already
contains byte-identical markdown, while still guaranteeing the file reflects
the rendered summary; the first write and any changed re-render still write.

The writer is exercised end-to-end through a counting in-memory FileBackend so
no real filesystem I/O is performed.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.pipeline.fan_out import write_parallel_summary_handoff

if TYPE_CHECKING:
    from collections.abc import Mapping


class _CountingBackend(FileBackend):
    """In-memory FileBackend that records write_text invocations."""

    def __init__(self) -> None:
        self._files: dict[Path, str] = {}
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


_SUMMARY: Mapping[str, object] = {
    "workers": [
        {
            "unit_id": "u1",
            "status": "succeeded",
            "artifact_count": 2,
            "final_message": None,
        },
        {
            "unit_id": "u2",
            "status": "failed",
            "artifact_count": 0,
            "final_message": "boom",
        },
    ],
    "any_failed": True,
    "all_succeeded": False,
    "verification": {"ran": True, "passed": False, "exit_code": 2},
}


def test_parallel_summary_handoff_renders_workers_and_verification() -> None:
    backend = _CountingBackend()
    workspace_root = Path("/virtual-ws")

    relative = write_parallel_summary_handoff(workspace_root, _SUMMARY, backend=backend)

    assert relative == ".agent/DEVELOPMENT_RESULT.md"
    rendered = backend.read_text(workspace_root / ".agent" / "DEVELOPMENT_RESULT.md")
    assert "# Parallel Development Summary" in rendered
    assert "- **u1**: succeeded (2 artifact(s))" in rendered
    assert "- **u2**: failed (0 artifact(s)) — boom" in rendered
    assert "- any_failed: true" in rendered
    assert "- all_succeeded: false" in rendered
    assert "Ran: yes — failed (exit code 2)" in rendered


def test_parallel_summary_handoff_skips_write_when_content_identical() -> None:
    backend = _CountingBackend()
    workspace_root = Path("/virtual-ws")

    write_parallel_summary_handoff(workspace_root, _SUMMARY, backend=backend)
    write_parallel_summary_handoff(workspace_root, _SUMMARY, backend=backend)

    assert len(backend.write_text_calls) == 1


def test_parallel_summary_handoff_writes_when_content_changed() -> None:
    backend = _CountingBackend()
    workspace_root = Path("/virtual-ws")
    changed = dict(_SUMMARY)
    changed["any_failed"] = False
    changed["all_succeeded"] = True

    write_parallel_summary_handoff(workspace_root, _SUMMARY, backend=backend)
    write_parallel_summary_handoff(workspace_root, changed, backend=backend)

    assert len(backend.write_text_calls) == 2
    rendered = backend.read_text(workspace_root / ".agent" / "DEVELOPMENT_RESULT.md")
    assert "- all_succeeded: true" in rendered
