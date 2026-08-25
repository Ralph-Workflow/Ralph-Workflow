"""A server subprocess that never becomes ready must report WHY, not just refuse.

The MCP server subprocess writes its stdout and stderr to
``<workspace>/.agent/tmp/mcp-server.log``. When the child died during startup --
for example because a configured custom MCP server was unreachable -- the parent
discarded that log and raised a bare ``did not become ready: ... Connection
refused``, which names the symptom and hides the cause.

The parent now appends the output the child produced during THIS spawn (bytes
appended after the spawn began, so a restart never replays a previous crash).
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol.env import MCP_SESSION_FILE_ENV
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.protocol.startup import PreflightError
from ralph.mcp.server import lifecycle
from ralph.mcp.server.lifecycle import (
    McpServerError,
    ProcessLike,
    SpawnProcess,
    mcp_server_log_path,
)

if TYPE_CHECKING:
    from pathlib import Path

_CHILD_DIAGNOSTIC = "UpstreamValidationError: custom MCP server 'docs-mcp' is unreachable"


class _LiveProcess:
    """A child that is still running when preflight gives up."""

    def __init__(self) -> None:
        self.pid = 4321

    def poll(self) -> int | None:
        return None

    def terminate(self, grace_period_s: float = 5.0) -> None:
        del grace_period_s

    def wait(self, timeout: float | None = None) -> int | None:
        del timeout
        return 0

    def kill(self) -> None:
        return None


def _deps(tmp_path: Path, spawn: SpawnProcess) -> lifecycle.LifecycleDeps:
    def fake_create_session_file(root: Path, session: object) -> Path:
        del root, session
        path = tmp_path / "session.json"
        path.write_text("{}", encoding="utf-8")
        return path

    def fake_preflight(endpoint: str, required_tools: list[str], timeout: timedelta) -> None:
        del required_tools, timeout
        raise PreflightError(f"failed to connect to MCP endpoint {endpoint}: [Errno 61] refused")

    return lifecycle.LifecycleDeps(
        reserve_port=lambda: 43199,
        create_session_file=fake_create_session_file,
        subprocess_env=lambda session_file: {str(MCP_SESSION_FILE_ENV): str(session_file)},
        spawn_process=spawn,
        preflight=fake_preflight,
        preflight_timeout=lambda: timedelta(seconds=5),
    )


def _session() -> AgentSession:
    return AgentSession(
        session_id="session-startup-diagnostics",
        run_id="run-startup-diagnostics",
        drain="development",
        capabilities={"WorkspaceRead"},
    )


def test_startup_failure_reports_the_child_diagnostic_output(tmp_path: Path) -> None:
    def spawn(
        command: list[str], cwd: Path, env: dict[str, str], *, phase: str | None = None
    ) -> ProcessLike:
        del command, env, phase
        log = mcp_server_log_path(cwd)
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            f"Traceback (most recent call last):\n{_CHILD_DIAGNOSTIC}\n", encoding="utf-8"
        )
        return _LiveProcess()

    with pytest.raises(McpServerError) as excinfo:
        lifecycle.start_mcp_server(
            _session(), lifecycle.FsWorkspace(tmp_path), deps=_deps(tmp_path, spawn)
        )

    assert _CHILD_DIAGNOSTIC in str(excinfo.value)


def test_startup_failure_omits_output_written_before_this_spawn(tmp_path: Path) -> None:
    stale = "UpstreamValidationError: custom MCP server 'previous-run' is unreachable"
    log = mcp_server_log_path(tmp_path)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(f"{stale}\n", encoding="utf-8")

    def spawn(
        command: list[str], cwd: Path, env: dict[str, str], *, phase: str | None = None
    ) -> ProcessLike:
        del command, env, phase
        with mcp_server_log_path(cwd).open("a", encoding="utf-8") as stream:
            stream.write(f"{_CHILD_DIAGNOSTIC}\n")
        return _LiveProcess()

    with pytest.raises(McpServerError) as excinfo:
        lifecycle.start_mcp_server(
            _session(), lifecycle.FsWorkspace(tmp_path), deps=_deps(tmp_path, spawn)
        )

    message = str(excinfo.value)
    assert _CHILD_DIAGNOSTIC in message
    assert stale not in message


def test_startup_failure_without_child_output_still_reports_the_endpoint(tmp_path: Path) -> None:
    def spawn(
        command: list[str], cwd: Path, env: dict[str, str], *, phase: str | None = None
    ) -> ProcessLike:
        del command, cwd, env, phase
        return _LiveProcess()

    with pytest.raises(McpServerError) as excinfo:
        lifecycle.start_mcp_server(
            _session(), lifecycle.FsWorkspace(tmp_path), deps=_deps(tmp_path, spawn)
        )

    assert "did not become ready" in str(excinfo.value)
