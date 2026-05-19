"""Tests for same-workspace parallel worker behaviour."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.pipeline.parallel.coordinator import (
    prepare_executor,
)
from ralph.pipeline.parallel.mode import SameWorkspaceContext
from ralph.pipeline.work_units import (
    WorkUnit,
)

if TYPE_CHECKING:
    from pathlib import Path
from tests._concurrent_fake_mcp_server_factory import _FakeMcpServerFactory


def _make_unit(unit_id: str, allowed_directories: list[str] | None = None) -> WorkUnit:
    dirs = allowed_directories if allowed_directories is not None else [f"src/{unit_id}"]
    return WorkUnit(
        unit_id=unit_id,
        description=f"Work unit {unit_id}",
        allowed_directories=dirs,
    )



class TestConcurrentWorkerArtifactIsolation:
    def test_concurrent_workers_write_to_separate_artifact_dirs(self, tmp_path: Path) -> None:
        """Each worker gets its own artifact directory;
        writing to one never appears in the other."""
        unit_a = _make_unit("unit-A")
        unit_b = _make_unit("unit-B", ["src/b"])
        mock_executor = MagicMock()
        ctx = _make_same_workspace_context(tmp_path, executor_command=None)

        _, _, ns_a = prepare_executor(unit_a, mock_executor, ctx)
        _, _, ns_b = prepare_executor(unit_b, mock_executor, ctx)

        assert ns_a is not None
        assert ns_b is not None

        # Write distinct artifacts to each namespace.
        artifact_a = ns_a / "artifacts" / "result.json"
        artifact_b = ns_b / "artifacts" / "result.json"
        artifact_a.write_text(json.dumps({"unit_id": "unit-A"}))
        artifact_b.write_text(json.dumps({"unit_id": "unit-B"}))

        # Each artifact decodes to its own unit_id.
        assert json.loads(artifact_a.read_text())["unit_id"] == "unit-A"
        assert json.loads(artifact_b.read_text())["unit_id"] == "unit-B"

        # The two paths are distinct directories.
        assert artifact_a.parent != artifact_b.parent

        # unit-A's artifact does NOT appear in unit-B's directory.
        assert (
            not (ns_b / "artifacts" / "result.json").read_text().startswith('{"unit_id": "unit-A"}')
        )


def _make_same_workspace_context(
    tmp_path: Path,
    *,
    executor_command: tuple[str, ...] | None = None,
) -> SameWorkspaceContext:
    return SameWorkspaceContext(
        repo_root=tmp_path,
        mcp_factory=_FakeMcpServerFactory(),
        executor_command=executor_command,
    )
