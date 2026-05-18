"""Tests for same-workspace parallel worker behaviour."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.mcp.tools.coordination import ToolError
from ralph.mcp.tools.workspace import handle_write_file
from ralph.pipeline.work_units import (
    WorkUnit,
)
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path



def _make_unit(unit_id: str, allowed_directories: list[str] | None = None) -> WorkUnit:
    dirs = allowed_directories if allowed_directories is not None else [f"src/{unit_id}"]
    return WorkUnit(
        unit_id=unit_id,
        description=f"Work unit {unit_id}",
        allowed_directories=dirs,
    )


class TestMcpToolBoundaryEnforcement:
    def test_mcp_write_tool_denied_outside_allowed_roots(self, tmp_path: Path) -> None:
        """handle_write_file raises ToolError when FsWorkspace rejects out-of-scope write."""

        allowed_dir = tmp_path / "src" / "allowed"
        allowed_dir.mkdir(parents=True)
        workspace = FsWorkspace(tmp_path, allowed_roots=(allowed_dir,))

        class _PermissiveSession:
            session_id = "test-session"
            is_parallel_worker = False

            def check_capability(self, _capability: str) -> object:
                return "approved"

        with pytest.raises(ToolError, match="Failed to write file"):
            handle_write_file(
                _PermissiveSession(),
                workspace,
                {"path": "src/other/output.txt", "content": "forbidden"},
            )

    def test_mcp_write_tool_succeeds_inside_allowed_roots(self, tmp_path: Path) -> None:
        """handle_write_file succeeds when FsWorkspace allows the target path."""

        allowed_dir = tmp_path / "src" / "allowed"
        allowed_dir.mkdir(parents=True)
        workspace = FsWorkspace(tmp_path, allowed_roots=(allowed_dir,))

        class _PermissiveSession:
            session_id = "test-session"
            is_parallel_worker = False

            def check_capability(self, _capability: str) -> object:
                return "approved"

        result = handle_write_file(
            _PermissiveSession(),
            workspace,
            {"path": "src/allowed/output.txt", "content": "permitted"},
        )
        assert result.is_error is False
        assert (allowed_dir / "output.txt").read_text() == "permitted"
