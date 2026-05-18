"""Tests for ralph/mcp/artifacts.py — MCP artifact management."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.mcp.artifacts.store import (
    list_artifacts,
    submit_artifact,
)

MULTI_ARTIFACT_COUNT = 2


class TestListArtifacts:
    def test_list_artifacts_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            artifacts = list_artifacts(artifact_dir)
            assert artifacts == []

    def test_list_artifacts_multiple(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            submit_artifact(artifact_dir, name="artifact_a", artifact_type="type_a", content={})
            submit_artifact(artifact_dir, name="artifact_b", artifact_type="type_b", content={})

            artifacts = list_artifacts(artifact_dir)

            assert len(artifacts) == MULTI_ARTIFACT_COUNT
            names = {a.name for a in artifacts}
            assert names == {"artifact_a", "artifact_b"}

    def test_list_artifacts_sorted_by_updated_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact_dir = Path(tmpdir) / "artifacts"

            submit_artifact(artifact_dir, name="first", artifact_type="code", content={})
            submit_artifact(artifact_dir, name="second", artifact_type="code", content={})

            artifacts = list_artifacts(artifact_dir)

            assert len(artifacts) == MULTI_ARTIFACT_COUNT
            assert artifacts[0].name == "first"
            assert artifacts[1].name == "second"
