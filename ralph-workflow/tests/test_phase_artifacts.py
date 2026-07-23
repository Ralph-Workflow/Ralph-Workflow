"""Tests for ralph/phases/artifacts.py — phase artifact helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from ralph.phases.artifacts import (
    PhaseArtifactError,
    load_phase_artifact,
    unwrap_phase_artifact_content,
)
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.memory import MemoryWorkspace


def test_load_phase_artifact_raises_phase_artifact_error_when_file_missing(
    tmp_path: Path,
) -> None:
    """Missing artifact file must raise PhaseArtifactError, not FileNotFoundError."""
    workspace = FsWorkspace(tmp_path)

    with pytest.raises(PhaseArtifactError, match="Artifact not found"):
        load_phase_artifact(workspace, ".agent/artifacts/missing.json")


def test_phase_artifacts_regression_loads_validated_markdown_as_legacy_envelope() -> None:
    """PLAN step 15: markdown phase input retains the JSON envelope contract."""
    workspace = MemoryWorkspace()
    workspace.write(
        ".agent/FIX_RESULT.md",
        """---
type: fix_result
---
## Summary
- [S1] Fixed validation
## Files Changed
- [F1] ralph/phases/artifacts.py
""",
    )

    artifact = load_phase_artifact(workspace, ".agent/FIX_RESULT.md")

    assert artifact == {
        "type": "fix_result",
        "content": {
            "summary": "Fixed validation",
            "files_changed": "- ralph/phases/artifacts.py",
        },
    }
    assert unwrap_phase_artifact_content(artifact, expected_type="fix_result") == artifact["content"]


def test_phase_artifacts_regression_reports_markdown_validation_errors() -> None:
    """PLAN step 15: invalid markdown cannot bypass the existing phase gate."""
    workspace = MemoryWorkspace()
    workspace.write(
        ".agent/FIX_RESULT.md",
        """---
type: fix_result
---
## Summary
- [S1] Fixed validation
""",
    )

    with pytest.raises(PhaseArtifactError, match=r"Markdown artifact .*missing required section"):
        load_phase_artifact(workspace, ".agent/FIX_RESULT.md")


def test_phase_artifacts_regression_keeps_legacy_json_artifacts_readable() -> None:
    """PLAN step 15: existing JSON artifacts remain valid phase input."""
    workspace = MemoryWorkspace()
    legacy = {"type": "fix_result", "content": {"summary": "Kept", "files_changed": "x.py"}}
    workspace.write(".agent/artifacts/fix_result.json", '{"type":"fix_result","content":{"summary":"Kept","files_changed":"x.py"}}')

    assert load_phase_artifact(workspace, ".agent/artifacts/fix_result.json") == legacy
