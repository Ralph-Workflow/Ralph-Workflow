"""Regression test: pre-serve blocking workspace read surfaces typed McpServerError.

Validates that real pre-serve registry dependencies (specifically the workspace
read for design-system policy during build_ralph_tool_registry) are bounded by
construction before preflight, and surface a typed McpServerError with
last_stage="registry_built" and elapsed diagnostics before the preflight deadline.

The in-process assertions use the PUBLIC seams only:

- the in-memory :class:`StageEventRecorder` (registered through
  ``stage_recorder_override``) observes the startup stage attribution. The
  previous assertion read the ON-DISK stage file, whose write latency (temp
  file + fsync + atomic replace) made the default-suite test load-sensitive:
  when the stage write overran the remaining startup budget the production
  fail-closed path called ``os._exit(34)`` and terminated the pytest
  controller instead of failing the test.
- ``exit_handler_override`` and ``overrun_handler_override`` replace that
  fail-closed boundary with a handler that raises a normal test failure, so a
  stalled stage write can NEVER terminate the pytest controller. The
  real-child fail-closed contract (the distinct per-stage exit codes) stays
  covered by the ``subprocess_e2e`` siblings in
  ``tests/mcp/test_bounded_catalog_publish.py`` and
  ``tests/mcp/test_whole_startup_budget_production.py``.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._pre_serve_overrun import (
    StageEventRecorder,
    exit_handler_override,
    overrun_handler_override,
    stage_recorder_override,
)
from ralph.mcp.server.runtime import McpServerExtras, build_standalone_http_server
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from ralph.mcp.server._exit_handler import ExitHandler
    from ralph.mcp.server._stage_recorder import StageRecorder

#: Startup stages the runtime records before the registry build starts. The
#: blocked workspace read happens INSIDE the registry build, so
#: ``registry_built`` is the last stage recorded before the typed error.
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
    """Fail the test instead of terminating the controller on the fail-closed exit.

    The production boundary is ``os._exit``. Running it inside the pytest
    controller would kill the whole session (and the shard) instead of
    reporting a failing test, so the in-process tests replace it with this
    handler.
    """
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
    """Fail the test instead of exiting the process on the pre-serve overrun path.

    The overrun seam runs BEFORE the exit seam, so registering it keeps the
    controller safe even if the exit handler were removed.
    """
    pytest.fail(
        "the production pre-serve overrun boundary was reached in-process "
        f"(stage={stage!r}, elapsed_s={elapsed_s:.3f}, budget_s={budget_s:.3f}, "
        f"exit_code={exit_code}, diagnostic_timeout_s={diagnostic_timeout_s}, "
        f"stage_recorder={stage_recorder!r}, exit_fn={exit_fn!r}); the "
        "blocked-workspace-read path must surface a typed McpServerError, "
        "never terminate the test controller"
    )


class _BlockingWorkspace(FsWorkspace):
    """Test double that blocks in read() until released or timeout."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.block_event = threading.Event()

    def read(self, path: str) -> str:
        self.block_event.wait(timeout=10.0)
        return super().read(path)


def test_injected_workspace_blocking_read_surfaces_typed_mcpservererror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Injected workspace whose read() blocks raises McpServerError(last_stage='registry_built')."""
    # Allow 2500ms preflight to satisfy the probe ceiling invariant
    # so remaining real startup deadline is ~0.5s (2.5s preflight - 2.0s probe safety).
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

        # The stage attribution is observed through the in-memory recorder:
        # no on-disk stage file is written, so a slow filesystem can neither
        # hide the attribution nor push the stage write past its budget.
        assert recorder.stages == _EXPECTED_STAGE_SEQUENCE, (
            f"unexpected startup stage sequence: {recorder.stages!r}"
        )
        assert recorder.last_stage == "registry_built", (
            f"expected last stage 'registry_built', got {recorder.last_stage!r}"
        )
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


@pytest.mark.subprocess_e2e
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
