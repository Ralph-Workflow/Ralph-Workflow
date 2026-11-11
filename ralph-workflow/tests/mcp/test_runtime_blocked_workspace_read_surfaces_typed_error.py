from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import (
    McpServerError,
    StageEventRecorder,
    exit_handler_override,
    lifecycle,
    overrun_handler_override,
    stage_recorder_override,
)
from ralph.mcp.server.runtime import McpServerExtras, build_standalone_http_server
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from ralph.mcp.server import ExitHandler, StageRecorder

_EXPECTED_STAGE_SEQUENCE: tuple[str, ...] = (
    "config_loading",
    "config_loaded",
    "upstream_catalog_or_discovery_done",
    "registry_built",
)


def _make_session() -> AgentSession:
    return AgentSession(
        session_id="test-blocked-workspace-read",
        run_id="run-blocked-workspace-read",
        drain="development",
        capabilities={"WorkspaceRead"},
    )


def _fail_closed_exit(exit_code: int) -> None:
    pytest.fail(
        "the production fail-closed exit boundary was reached in-process "
        f"(exit_code={exit_code}); the blocked-workspace-read path must "
        "surface a typed McpServerError, never terminate the test controller"
    )


def _fail_closed_overrun(
    workspace_root: Path,
    *,
    stage: str,
    elapsed_s: float,
    budget_s: float,
    exit_code: int,
    diagnostic_timeout_s: float | None = None,
    stage_recorder: StageRecorder | None = None,
    exit_fn: ExitHandler | None = None,
) -> None:
    pytest.fail(
        "the production pre-serve overrun boundary was reached in-process "
        f"(stage={stage!r}, elapsed_s={elapsed_s:.3f}, budget_s={budget_s:.3f}, "
        f"exit_code={exit_code}, diagnostic_timeout_s={diagnostic_timeout_s}, "
        f"stage_recorder={stage_recorder!r}, exit_fn={exit_fn!r}); the "
        "blocked-workspace-read path must surface a typed McpServerError, "
        "never terminate the test controller"
    )


class _BlockingWorkspace(FsWorkspace):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.block_event = threading.Event()

    def read(self, path: str) -> str:
        self.block_event.wait(timeout=10.0)
        return super().read(path)


def test_injected_workspace_blocking_read_surfaces_typed_mcpservererror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ralph.mcp.protocol._startup_timeouts._PROBE_BUDGET_SHARE", 0.9)
    blocking_ws = _BlockingWorkspace(tmp_path)
    session = _make_session()
    recorder = StageEventRecorder()

    try:
        with (
            stage_recorder_override(recorder),
            overrun_handler_override(_fail_closed_overrun),
            exit_handler_override(_fail_closed_exit),
            pytest.raises(McpServerError) as exc_info,
        ):
            build_standalone_http_server(
                tmp_path,
                workspace=blocking_ws,
                extras=McpServerExtras(session=session),
                env={"RALPH_MCP_PREFLIGHT_TIMEOUT_MS": "2500"},
            )

        err = exc_info.value
        assert err.last_stage == "registry_built", (
            f"expected 'registry_built', got {err.last_stage!r}"
        )
        assert err.elapsed_s is not None, "elapsed_s must be recorded"
        assert err.elapsed_s > 0, f"elapsed_s must be > 0, got {err.elapsed_s}"
        assert err.elapsed_s < 2.0, f"elapsed_s must be fail-fast, got {err.elapsed_s}"

        assert recorder.stages == _EXPECTED_STAGE_SEQUENCE, (
            f"unexpected startup stage sequence: {recorder.stages!r}"
        )
        assert recorder.last_stage == "registry_built", (
            f"expected last stage 'registry_built', got {recorder.last_stage!r}"
        )
    finally:
        blocking_ws.block_event.set()


def test_build_ralph_tool_registry_bounds_blocking_workspace_read(tmp_path: Path) -> None:
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


@pytest.mark.subprocess_e2e
def test_blocked_workspace_read_in_start_mcp_server_raises_typed_error(tmp_path: Path) -> None:
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
