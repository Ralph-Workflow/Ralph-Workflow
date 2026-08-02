"""Atomic persistence regressions for canonical artifact submission."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.mcp.artifacts import submit_artifact_canonical
from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from tests._artifact_format_docs_memory_backend import MemoryBackend
from tests.test_canonical_artifact_submit import DEVELOPMENT_RESULT, _parsed

if TYPE_CHECKING:
    from pathlib import Path


class _AtomicityBackend(MemoryBackend):
    """Memory backend that exposes canonical writes and replace faults."""

    def __init__(self, *, fail_replace: bool = False, corrupt_replace: bool = False) -> None:
        super().__init__()
        self.fail_replace = fail_replace
        self.corrupt_replace = corrupt_replace

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        if path.name in {"development_result.md", "DEVELOPMENT_RESULT.md"}:
            raise AssertionError("canonical destinations must be replaced, not directly written")
        super().write_text(path, content, encoding=encoding)

    def replace(self, source: Path, destination: Path) -> None:
        if self.fail_replace:
            raise OSError("interrupted before replacement")
        super().replace(source, destination)
        if self.corrupt_replace:
            self.corrupt_replace = False
            self._files[destination] = "corrupt"


def _submit(tmp_path: Path, backend: MemoryBackend, markdown: str) -> None:
    submit_artifact_canonical(
        workspace_root=tmp_path,
        artifact_type="development_result",
        parsed_content=_parsed("development_result", markdown),
        markdown=markdown,
        deps=ArtifactHandlerDeps(backend=backend),
        run_id="run-atomic",
    )


def test_canonical_submit_regression_writes_artifact_and_handoff_via_replace(
    tmp_path: Path,
) -> None:
    """S-1: destination write faults prove both canonical files use replacement."""
    backend = _AtomicityBackend()

    _submit(tmp_path, backend, DEVELOPMENT_RESULT)

    assert (
        backend.read_text(tmp_path / ".agent" / "artifacts" / "development_result.md")
        == DEVELOPMENT_RESULT
    )
    assert backend.read_text(tmp_path / ".agent" / "DEVELOPMENT_RESULT.md") == DEVELOPMENT_RESULT


def test_canonical_submit_regression_interrupted_replace_keeps_previous_complete_files(
    tmp_path: Path,
) -> None:
    """S-1: a kill before replace leaves existing artifact and handoff intact."""
    backend = _AtomicityBackend()
    _submit(tmp_path, backend, DEVELOPMENT_RESULT)
    backend.fail_replace = True
    updated = DEVELOPMENT_RESULT.replace("Completed the Markdown migration.", "Updated plan.")

    with pytest.raises(OSError, match="interrupted before replacement"):
        _submit(tmp_path, backend, updated)

    assert (
        backend.read_text(tmp_path / ".agent" / "artifacts" / "development_result.md")
        == DEVELOPMENT_RESULT
    )
    assert backend.read_text(tmp_path / ".agent" / "DEVELOPMENT_RESULT.md") == DEVELOPMENT_RESULT


def test_canonical_submit_regression_corrupt_write_rolls_back_without_receipt(
    tmp_path: Path,
) -> None:
    """S-1: corrupted replacement raises and restores both prior complete documents."""
    backend = _AtomicityBackend()
    _submit(tmp_path, backend, DEVELOPMENT_RESULT)
    backend.corrupt_replace = True
    updated = DEVELOPMENT_RESULT.replace("Completed the Markdown migration.", "Updated plan.")

    with pytest.raises(OSError, match="corrupt"):
        _submit(tmp_path, backend, updated)

    assert (
        backend.read_text(tmp_path / ".agent" / "artifacts" / "development_result.md")
        == DEVELOPMENT_RESULT
    )
    assert backend.read_text(tmp_path / ".agent" / "DEVELOPMENT_RESULT.md") == DEVELOPMENT_RESULT
