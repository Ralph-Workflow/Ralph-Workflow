"""Tests for same-workspace parallel worker behaviour."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

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


class TestSameWorkspaceContext:
    def test_worker_namespace_root_defaults_to_dot_agent_workers(self, tmp_path: Path) -> None:
        mock_factory = MagicMock()
        ctx = SameWorkspaceContext(repo_root=tmp_path, mcp_factory=mock_factory)
        assert ctx.worker_namespace_root == tmp_path / ".agent" / "workers"

    def test_worker_namespace_root_can_be_overridden(self, tmp_path: Path) -> None:
        mock_factory = MagicMock()
        custom_ns = tmp_path / "custom" / "workers"
        ctx = SameWorkspaceContext(
            repo_root=tmp_path,
            mcp_factory=mock_factory,
            worker_namespace_root=custom_ns,
        )
        assert ctx.worker_namespace_root == custom_ns
