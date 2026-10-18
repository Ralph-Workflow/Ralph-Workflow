"""Production cumulative spawn-to-serving startup deadline tests.

Tests both the in-process cumulative deadline enforcement across all stage
boundaries (env_loaded -> session_ready -> config_loading -> config_loaded
-> upstream_catalog_or_discovery_done -> registry_built -> ready_to_serve)
and the black-box real child behavior where a client connection succeeds in
the kernel backlog while the child is slowed past budget and fails preflight
with typed McpServerError.
"""

from __future__ import annotations

import contextlib
import os
import socket
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from ralph.mcp.protocol._startup_timeouts import (
    mcp_preflight_timeout_from_env,
    mcp_whole_startup_budget_from_env,
)
from ralph.mcp.protocol.capability_mapping import Capability
from ralph.mcp.protocol.env import (
    MCP_INHERITED_FD_ENV,
    MCP_PREFLIGHT_TIMEOUT_MS_ENV,
    MCP_STAGE_ID_ENV,
    MCP_TEST_PRE_SERVE_GATE_ENV,
)
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import runtime as runtime_module
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._start_stage_tracker import read_last_stage
from ralph.mcp.server.lifecycle import session_payload_json
from ralph.mcp.server.runtime import McpServerExtras, run_standalone_server
from ralph.process.manager import SpawnOptions, get_process_manager


def _setup_session(tmp_path: Path) -> dict[str, str]:
    session = AgentSession(
        session_id="test-session-whole-budget",
        run_id="test-run-whole-budget",
        drain="test",
        capabilities={cap.value for cap in Capability},
    )
    session_file = tmp_path / "session.json"
    session_file.write_text(session_payload_json(session), encoding="utf-8")
    return {
        "RALPH_MCP_SESSION_FILE": str(session_file),
    }


def test_import_time_invariant_whole_startup_budget_less_than_preflight() -> None:
    """The whole startup budget must be strictly smaller than the preflight timeout."""
    whole_budget = mcp_whole_startup_budget_from_env()
    preflight_timeout = mcp_preflight_timeout_from_env().total_seconds()
    assert whole_budget < preflight_timeout


def test_cumulative_deadline_enforces_at_session_ready(tmp_path: Path) -> None:
    """Cumulative deadline exceeded during session loading raises McpServerError."""
    env = _setup_session(tmp_path)
    whole_budget = mcp_whole_startup_budget_from_env(env)
    past_budget = whole_budget + 1.0

    ticks = iter((0.0, past_budget, past_budget * 2, past_budget * 3))
    with (
        patch.object(runtime_module, "_clock", lambda: next(ticks)),
        pytest.raises(McpServerError) as exc_info,
    ):
        run_standalone_server(tmp_path, env=env, port=0)

    err = exc_info.value
    assert err.last_stage in ("env_loaded", "session_ready")
    assert err.elapsed_s is not None and err.elapsed_s >= whole_budget


def test_cumulative_deadline_enforces_at_config_loading(tmp_path: Path) -> None:
    """Cumulative deadline exceeded before config loading raises McpServerError."""
    whole_budget = mcp_whole_startup_budget_from_env({})
    past_budget = whole_budget + 1.0

    # First tick 0.0 (t0), second tick past_budget (at config_loading check)
    ticks = iter((0.0, past_budget, past_budget * 2, past_budget * 3))
    session = AgentSession(
        session_id="test-session",
        run_id="test-run",
        drain="standalone",
        capabilities={"mcp"},
    )
    with (
        patch.object(runtime_module, "_clock", lambda: next(ticks)),
        pytest.raises(McpServerError) as exc_info,
    ):
        runtime_module.build_standalone_http_server(
            workspace_root=tmp_path,
            extras=McpServerExtras(session=session),
            env={},
        )

    err = exc_info.value
    assert err.last_stage == "config_loading"
    assert err.elapsed_s is not None and err.elapsed_s >= whole_budget


def test_cumulative_deadline_enforces_at_ready_to_serve(tmp_path: Path) -> None:
    """Cumulative deadline exceeded at the ready_to_serve boundary raises McpServerError."""
    env = _setup_session(tmp_path)
    whole_budget = mcp_whole_startup_budget_from_env(env)

    # Calling _check_cumulative_startup_deadline with an expired t0 raises typed McpServerError
    expired_t0 = time.monotonic() - whole_budget - 1.0
    with pytest.raises(McpServerError) as exc_info:
        runtime_module._check_cumulative_startup_deadline("ready_to_serve", expired_t0, env)

    err = exc_info.value
    assert err.last_stage == "ready_to_serve"
    assert err.elapsed_s is not None and err.elapsed_s > whole_budget


@pytest.mark.timeout_seconds(20)
@pytest.mark.subprocess_e2e
def test_black_box_real_child_slowed_past_budget_fails_preflight(tmp_path: Path) -> None:
    """Real child slowed past budget fails with McpServerError while client connects in backlog.

    Black box contract:
    1. Parent binds and listens on a socket and hands it via RALPH_MCP_INHERITED_FD.
    2. Child adopts the inherited listening socket.
    3. A client connects over TCP: connection succeeds in kernel backlog while child is held.
    4. Child is slowed past whole startup budget via RALPH_MCP_TEST_PRE_SERVE_GATE.
    5. Gate is released; child checks cumulative deadline at serving boundary and fails
       closed with typed McpServerError.
    6. "listening" banner is NEVER emitted.
    """
    spawn_id = str(uuid.uuid4())
    sentinel = tmp_path / "pre_serve_gate"
    env = _setup_session(tmp_path)

    # Create and bind parent listening socket
    parent_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    parent_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    parent_sock.bind(("127.0.0.1", 0))
    parent_sock.listen(128)
    addr: tuple[str, int] = parent_sock.getsockname()[:2]
    host, port = addr

    child_env = dict(os.environ)
    child_env.update(env)
    child_env[MCP_INHERITED_FD_ENV] = str(parent_sock.fileno())
    child_env[MCP_STAGE_ID_ENV] = spawn_id
    child_env[MCP_TEST_PRE_SERVE_GATE_ENV] = str(sentinel)
    # Set preflight to 4500ms so whole startup budget is 4.3s (< 4.5s)
    child_env[MCP_PREFLIGHT_TIMEOUT_MS_ENV] = "4500"

    proc = get_process_manager().spawn(
        [
            sys.executable,
            "-m",
            "ralph.mcp.server",
            "--workspace",
            str(tmp_path),
            "--host",
            host,
            "--port",
            str(port),
        ],
        SpawnOptions(
            cwd=str(tmp_path),
            env=child_env,
            pass_fds=(parent_sock.fileno(),),
            label="mcp-slow-child",
        ),
    )

    client_sock: socket.socket | None = None
    try:
        # Wait until child has started and reached registry_built stage
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            stage = read_last_stage(tmp_path, spawn_id=spawn_id)
            if stage == "registry_built":
                break
            time.sleep(0.05)

        assert read_last_stage(tmp_path, spawn_id=spawn_id) == "registry_built"

        # Client connects: TCP handshake succeeds in kernel backlog because socket is listening
        client_sock = socket.create_connection((host, port), timeout=5.0)
        assert client_sock is not None

        # Hold child until past the 4.3s cumulative startup budget
        # We need elapsed time from spawn to exceed 4.3s
        time.sleep(4.4)

        # Release the gate: child unblocks, attempts to serve, checks cumulative startup deadline
        sentinel.touch()

        # Child must exit with non-zero status
        rc = proc.wait(timeout=10.0)
        assert rc != 0, f"child should have failed with McpServerError, but exited with {rc}"

        # Child stage file remains registry_built (never wrote ready_to_serve)
        last_recorded = read_last_stage(tmp_path, spawn_id=spawn_id)
        assert last_recorded == "registry_built"
    finally:
        if client_sock is not None:
            with contextlib.suppress(OSError):
                client_sock.close()
        with contextlib.suppress(OSError):
            parent_sock.close()
        with contextlib.suppress(Exception):
            proc.kill()
