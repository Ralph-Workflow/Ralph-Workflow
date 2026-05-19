"""Tests for ralph/mcp/artifacts.py — MCP artifact management."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ralph.mcp.artifacts.store import (
    ArtifactNotFoundError,
    ArtifactSubmitOptions,
    ArtifactUpdateOptions,
    submit_artifact,
    update_artifact,
)


class TestUpdateArtifact:
    def test_update_artifact_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            submit_artifact(
                artifact_dir,
                name="updatable",
                artifact_type="code",
                content={"original": True},
            )

            artifact = update_artifact(
                artifact_dir,
                name="updatable",
                options=ArtifactUpdateOptions(content={"original": True, "updated": True}),
            )

            assert artifact.content == {"original": True, "updated": True}

    def test_update_artifact_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            submit_artifact(
                artifact_dir,
                name="meta_update",
                artifact_type="code",
                content={},
                options=ArtifactSubmitOptions(metadata={"v": 1}),
            )

            artifact = update_artifact(
                artifact_dir,
                name="meta_update",
                options=ArtifactUpdateOptions(metadata={"v": 2, "new": True}),
            )

            assert artifact.metadata == {"v": 2, "new": True}

    def test_update_artifact_not_found_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            with pytest.raises(ArtifactNotFoundError):
                update_artifact(
                    artifact_dir,
                    name="nonexistent",
                    options=ArtifactUpdateOptions(content={"x": 1}),
                )
