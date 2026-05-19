"""Tests for ralph/mcp/artifacts.py — MCP artifact management."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ralph.mcp.artifacts.store import (
    ArtifactNotFoundError,
    get_artifact,
    submit_artifact,
)


class TestGetArtifact:
    def test_get_artifact_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            submit_artifact(
                artifact_dir,
                name="retrieve_me",
                artifact_type="review",
                content={"rating": 5},
            )

            artifact = get_artifact(artifact_dir, "retrieve_me")

            assert artifact.name == "retrieve_me"
            assert artifact.artifact_type == "review"
            assert artifact.content == {"rating": 5}

    def test_get_artifact_not_found_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            with pytest.raises(ArtifactNotFoundError) as exc_info:
                get_artifact(artifact_dir, "nonexistent")
            assert "not found" in str(exc_info.value)
