"""Tests for ralph/mcp/artifacts.py — MCP artifact management."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ralph.mcp.artifacts.store import (
    ArtifactNotFoundError,
    delete_artifact,
    list_artifacts,
    submit_artifact,
)


class TestDeleteArtifact:
    def test_delete_artifact_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            submit_artifact(artifact_dir, name="to_delete", artifact_type="code", content={})

            delete_artifact(artifact_dir, "to_delete")

            artifacts = list_artifacts(artifact_dir)
            assert artifacts == []

    def test_delete_artifact_not_found_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            with pytest.raises(ArtifactNotFoundError):
                delete_artifact(artifact_dir, "nonexistent")
