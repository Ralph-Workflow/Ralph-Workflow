"""Regression test: pre-serve blocking workspace read surfaces typed McpServerError.

Validates that real pre-serve registry dependencies (specifically the workspace
read for design-system policy during build_ralph_tool_registry) are bounded by
construction before preflight, and surface a typed McpServerError with
last_stage="registry_built" and elapsed diagnostics before the preflight deadline.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._start_stage_tracker import read_last_stage
from ralph.mcp.server.runtime import McpServerExtras, build_standalone_http_server
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.workspace.fs import FsWorkspace


def _make_session() -> AgentSession:
    return AgentSession(
        session_id="test-blocked-workspace-read",
        run_id="run-blocked-workspace-read",
        drain="development",
        capabilities={"WorkspaceRead"},
    )


class _BlockingWorkspace(FsWorkspace):
    """Test double that blocks in read() until released or timeout."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.block_event = threading.Event()

    def read(self, path: str) -> str:
        self.block_event.wait(timeout=10.0)
        return super().read(path)


def test_injected_workspace_blocking_read_surfaces_typed_mcpservererror(tmp_path: Path) -> None:
    """Injected workspace whose read() blocks raises McpServerError(last_stage='registry_built')."""
    blocking_ws = _BlockingWorkspace(tmp_path)
    session = _make_session()

    try:
        with pytest.raises(McpServerError) as exc_info:
            build_standalone_http_server(
                tmp_path,
                workspace=blocking_ws,
                extras=McpServerExtras(session=session),
                env={"RALPH_MCP_REGISTRY_READ_TIMEOUT_MS": "50"},
            )

        err = exc_info.value
        assert err.last_stage == "registry_built", (
            f"expected 'registry_built', got {err.last_stage!r}"
        )
        assert err.elapsed_s is not None, "elapsed_s must be recorded"
        assert err.elapsed_s > 0, f"elapsed_s must be > 0, got {err.elapsed_s}"
        assert err.elapsed_s < 2.0, f"elapsed_s must be fail-fast, got {err.elapsed_s}"

        # Assert stage file recorded registry_built before failure
        stage = read_last_stage(tmp_path)
        assert stage == "registry_built", f"expected stage file 'registry_built', got {stage!r}"
    finally:
        blocking_ws.block_event.set()


def test_build_ralph_tool_registry_bounds_blocking_workspace_read(tmp_path: Path) -> None:
    """build_ralph_tool_registry bounds blocking workspace read and raises typed McpServerError."""
    blocking_ws = _BlockingWorkspace(tmp_path)
    session = _make_session()

    try:
        with pytest.raises(McpServerError) as exc_info:
            build_ralph_tool_registry(
                session,
                blocking_ws,
                timeout_s=0.05,
            )

        err = exc_info.value
        assert err.last_stage == "registry_built"
        assert err.elapsed_s is not None
        assert err.elapsed_s >= 0.04
    finally:
        blocking_ws.block_event.set()


def test_blocked_workspace_read_in_start_mcp_server_raises_typed_error(tmp_path: Path) -> None:
    """When workspace policy read blocks, start_mcp_server raises typed McpServerError without hanging."""
    policy_dir = tmp_path / "docs" / "ralph-workflow-policy"
    policy_dir.mkdir(parents=True)
    policy_fifo = policy_dir / "design-system-policy.md"
    os.mkfifo(policy_fifo)

    session = _make_session()
    workspace = FsWorkspace(tmp_path)

    try:
        with pytest.raises(McpServerError) as exc_info:
            lifecycle.start_mcp_server(session, workspace)

        exc = exc_info.value
        assert exc.last_stage == "registry_built", (
            f"expected last_stage='registry_built', got {exc.last_stage!r}"
        )
        assert exc.elapsed_s is not None, "elapsed_s must not be None"
        assert exc.elapsed_s > 0, f"elapsed_s must be > 0, got {exc.elapsed_s}"
    finally:
        policy_fifo.unlink(missing_ok=True)
