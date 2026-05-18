"""Tests for same-workspace parallel worker behaviour."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.mcp.server.factory import McpServerHandle
from ralph.pipeline.parallel.coordinator import (
    prepare_executor,
)
from ralph.pipeline.parallel.mode import SameWorkspaceContext
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


class TestWorkerArtifactIsolation:
    class _FakeMcpServerFactory:
        def build(self, session: object) -> McpServerHandle:
            return McpServerHandle(
                endpoint="http://127.0.0.1:9999/mcp",
                pid=99999,
                shutdown=lambda: None,
            )

    def test_per_worker_artifact_dirs_are_separate(self, tmp_path: Path) -> None:
        unit_a = _make_unit("unit-a")
        unit_b = _make_unit("unit-b", ["src/b"])
        mock_executor = MagicMock()
        ctx_a = _make_same_workspace_context(tmp_path, executor_command=None)

        prepare_executor(unit_a, mock_executor, ctx_a)
        prepare_executor(unit_b, mock_executor, ctx_a)

        ns_root = ctx_a.worker_namespace_root
        assert (ns_root / "unit-a" / "artifacts").is_dir()
        assert (ns_root / "unit-b" / "artifacts").is_dir()
        # Namespaces are separate
        assert ns_root / "unit-a" != ns_root / "unit-b"


_FakeMcpServerFactory = TestWorkerArtifactIsolation._FakeMcpServerFactory


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
