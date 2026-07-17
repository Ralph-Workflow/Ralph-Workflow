"""Black-box tests for write_plan_markdown idempotent-write guard.

Satisfies AC-03: write_plan_markdown skips the physical write to
``.agent/PLAN.md`` when it already contains byte-identical
``render_plan_markdown`` output, guaranteeing the file content is
unchanged in the skip case.

The plan markdown writer is exercised end-to-end through a counting
in-memory FileBackend so no real filesystem I/O is performed (no
``tmp_path``, no ``Path.read_text``/``Path.write_text``, no patching).

``render_plan_markdown`` runs ``PlanArtifact`` validation first, so
the minimal payload below satisfies every required field on the
canonical plan schema (``scope_items`` min length 3, ``skills_mcp.skills``
min length 1, ``steps`` min length 1, ``critical_files.primary_files``
min length 1, ``risks_mitigations`` min length 1,
``verification_strategy`` min length 1).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.mcp.artifacts.plan._renderers import (
    render_plan_markdown,
    write_plan_markdown,
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


def _minimal_plan_payload(context_text: str) -> Mapping[str, object]:
    """Return a minimal valid plan payload whose render varies by ``context_text``."""
    payload: dict[str, object] = {
        "summary": {
            "context": context_text,
            "intent_verb": "improve",
            "scope_items": [
                {"text": "scope item one", "category": "refactor"},
                {"text": "scope item two", "category": "test"},
                {"text": "scope item three", "category": "cleanup"},
            ],
        },
        "skills_mcp": {"skills": ["test-driven-development"]},
        "steps": [
            {
                "number": 1,
                "title": "Minimal step",
                "content": "Carry out the minimal work.",
                "step_type": "action",
            }
        ],
        "critical_files": {
            "primary_files": [{"path": "src/example.py", "action": "modify"}]
        },
        "risks_mitigations": [
            {"risk": "Risk", "mitigation": "Mitigation"}
        ],
        "verification_strategy": [
            {"method": "make test", "expected_outcome": "all tests pass"}
        ],
    }
    return cast("Mapping[str, object]", payload)


def test_plan_markdown_regression_skips_write_when_content_identical() -> None:
    """AC-03: identical ``.agent/PLAN.md`` re-render performs zero additional writes.

    Verifies the skip half of AC-03: ``write_plan_markdown`` skips the
    physical write to ``.agent/PLAN.md`` when the destination already
    contains byte-identical ``render_plan_markdown`` output. The first
    call writes; the second identical call is a skip. The final
    ``_files[destination]`` equals the renderer output.
    """
    backend = _CountingBackend()
    workspace_root = Path("/virtual-ws")
    content = _minimal_plan_payload("ctx-original")
    destination = workspace_root / ".agent" / "PLAN.md"
    expected = render_plan_markdown(content)

    write_plan_markdown(workspace_root, content, backend=backend)
    write_plan_markdown(workspace_root, content, backend=backend)

    assert len(backend.write_text_calls) == 1
    assert backend.write_text_calls[0] == (destination, expected)
    assert backend._files[destination] == expected


def test_plan_markdown_regression_writes_when_content_changed() -> None:
    """AC-03: changed plan payload re-fires the write so ``.agent/PLAN.md`` reflects the new render.

    Verifies the changed-content half of AC-03: any re-render whose
    ``render_plan_markdown`` output differs triggers a fresh
    ``write_text`` call, and the final stored ``.agent/PLAN.md`` content
    equals ``render_plan_markdown`` of the changed payload.
    """
    backend = _CountingBackend()
    workspace_root = Path("/virtual-ws")
    initial = _minimal_plan_payload("ctx-original")
    changed = _minimal_plan_payload("ctx-updated")
    destination = workspace_root / ".agent" / "PLAN.md"
    expected = render_plan_markdown(changed)

    write_plan_markdown(workspace_root, initial, backend=backend)
    write_plan_markdown(workspace_root, changed, backend=backend)

    assert len(backend.write_text_calls) == 2
    assert backend.write_text_calls[1] == (destination, expected)
    assert backend._files[destination] == expected
