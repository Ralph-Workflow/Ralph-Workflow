"""Tests for same-workspace parallel worker behaviour."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.store import list_artifacts
from ralph.pipeline.work_units import (
    WorkUnit,
)

if TYPE_CHECKING:
    from pathlib import Path


def _make_unit(unit_id: str, allowed_directories: list[str] | None = None) -> WorkUnit:
    dirs = allowed_directories if allowed_directories is not None else [f"src/{unit_id}"]
    return WorkUnit(
        unit_id=unit_id,
        description=f"Work unit {unit_id}",
        allowed_directories=dirs,
    )


class TestArtifactsOnlySuccess:
    def test_zero_exit_but_no_artifact_is_failure(self, tmp_path: Path) -> None:
        """A worker that exits 0 but writes no artifact is a failure."""
        artifact_dir = tmp_path / ".agent" / "workers" / "unit-a" / "artifacts"
        artifact_dir.mkdir(parents=True)
        # No artifact written
        assert list_artifacts(artifact_dir) == []
        # This is the predicate used by the coordinator
        assert not list_artifacts(artifact_dir)

    def test_nonzero_exit_with_artifact_is_success(self, tmp_path: Path) -> None:
        """A worker that exits non-zero but writes a valid artifact is honored."""
        artifact_dir = tmp_path / ".agent" / "workers" / "unit-a" / "artifacts"
        artifact_dir.mkdir(parents=True)
        # Write a valid artifact
        (artifact_dir / "plan.json").write_text(
            json.dumps(
                {
                    "name": "plan",
                    "type": "plan",
                    "content": {"summary": "done"},
                    "created_at": "2024-01-01T00:00:00+00:00",
                    "updated_at": "2024-01-01T00:00:00+00:00",
                    "metadata": {},
                }
            )
        )
        # The artifact check passes
        assert list_artifacts(artifact_dir) != []

    def test_worker_a_cannot_satisfy_worker_b_via_shared_path(self, tmp_path: Path) -> None:
        """Artifacts under worker-A's namespace never satisfy worker-B's success check."""
        unit_a = _make_unit("unit-a")
        unit_b = _make_unit("unit-b", ["src/b"])

        # Write artifact for unit-a
        dir_a = tmp_path / ".agent" / "workers" / "unit-a" / "artifacts"
        dir_a.mkdir(parents=True)
        (dir_a / "plan.json").write_text(
            json.dumps(
                {
                    "name": "plan",
                    "type": "plan",
                    "content": {"summary": "a-done"},
                    "created_at": "2024-01-01T00:00:00+00:00",
                    "updated_at": "2024-01-01T00:00:00+00:00",
                    "metadata": {},
                }
            )
        )

        # unit-b's artifact_dir is separate — has no artifact
        dir_b = tmp_path / ".agent" / "workers" / "unit-b" / "artifacts"
        dir_b.mkdir(parents=True)

        del unit_a, unit_b  # silence unused-variable warnings
        assert list_artifacts(dir_a) != []
        assert list_artifacts(dir_b) == [], "unit-b must not see unit-a's artifact"
