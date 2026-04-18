"""Tests for ralph/phases/artifacts.py — phase artifact helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from ralph.phases.artifacts import PhaseArtifactError, load_phase_artifact
from ralph.workspace.fs import FsWorkspace


def test_load_phase_artifact_raises_phase_artifact_error_when_file_missing(
    tmp_path: Path,
) -> None:
    """Missing artifact file must raise PhaseArtifactError, not FileNotFoundError."""
    workspace = FsWorkspace(tmp_path)

    with pytest.raises(PhaseArtifactError, match="Artifact not found"):
        load_phase_artifact(workspace, ".agent/artifacts/missing.json")
