"""Tests for fail-closed invalid inherited FD adoption contract.

When ``RALPH_MCP_INHERITED_FD`` is set in the environment but the FD fails
validation (not a digit, closed FD, or not a socket) or adoption, the child
server MUST fail closed by raising typed ``McpServerError`` with
``last_stage="fd_inherit_failed"`` and recording it to the stage file.
When the environment variable is absent, the server keeps its fallback to a
fresh bind.
"""

from __future__ import annotations

import contextlib
import os
import socket
import sys
import threading
import uuid
from pathlib import Path

import pytest

from ralph.mcp.protocol.capability_mapping import Capability
from ralph.mcp.protocol.env import MCP_INHERITED_FD_ENV, MCP_STAGE_ID_ENV
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._start_stage_tracker import read_last_stage
from ralph.mcp.server.lifecycle import session_payload_json
from ralph.mcp.server.runtime import run_standalone_server
from ralph.process.manager import SpawnOptions, get_process_manager

pytestmark = pytest.mark.required_auto_integrate_e2e


def _setup_session(tmp_path: Path) -> dict[str, str]:
    session = AgentSession(
        session_id="test-session-inherited-fd",
        run_id="test-run-inherited-fd",
        drain="test",
        capabilities={cap.value for cap in Capability},
    )
    session_file = tmp_path / "session.json"
    session_file.write_text(session_payload_json(session), encoding="utf-8")
    return {
        "RALPH_MCP_SESSION_FILE": str(session_file),
    }


def test_invalid_inherited_fd_non_digit_fails_closed(tmp_path: Path) -> None:
    """When MCP_INHERITED_FD_ENV is not a non-negative integer string, raise McpServerError."""
    env = _setup_session(tmp_path)
    env[MCP_INHERITED_FD_ENV] = "not_a_digit"

    with pytest.raises(McpServerError) as exc_info:
        run_standalone_server(tmp_path, env=env, port=0)

    err = exc_info.value
    assert err.last_stage == "fd_inherit_failed"
    assert read_last_stage(tmp_path) == "fd_inherit_failed"


def test_invalid_inherited_fd_closed_fd_fails_closed(tmp_path: Path) -> None:
    """When MCP_INHERITED_FD_ENV points to a closed/invalid FD number, raise McpServerError."""
    env = _setup_session(tmp_path)
    env[MCP_INHERITED_FD_ENV] = "99999"

    with pytest.raises(McpServerError) as exc_info:
        run_standalone_server(tmp_path, env=env, port=0)

    err = exc_info.value
    assert err.last_stage == "fd_inherit_failed"
    assert read_last_stage(tmp_path) == "fd_inherit_failed"


def test_invalid_inherited_fd_not_a_socket_fails_closed(tmp_path: Path) -> None:
    """When MCP_INHERITED_FD_ENV points to a regular file descriptor, raise McpServerError."""
    env = _setup_session(tmp_path)
    file_path = tmp_path / "sample.txt"
    file_path.write_text("hello", encoding="utf-8")

    fd = os.open(str(file_path), os.O_RDONLY)
    try:
        env[MCP_INHERITED_FD_ENV] = str(fd)
        with pytest.raises(McpServerError) as exc_info:
            run_standalone_server(tmp_path, env=env, port=0)

        err = exc_info.value
        assert err.last_stage == "fd_inherit_failed"
        assert read_last_stage(tmp_path) == "fd_inherit_failed"
    finally:
        os.close(fd)


def test_absent_inherited_fd_falls_back_to_fresh_bind(tmp_path: Path) -> None:
    """When MCP_INHERITED_FD_ENV is absent, the server binds freshly without error."""
    env = _setup_session(tmp_path)
    # Ensure MCP_INHERITED_FD_ENV is not in env
    env.pop(MCP_INHERITED_FD_ENV, None)

    ready_event = threading.Event()
    stop_event = threading.Event()

    def _run() -> None:
        run_standalone_server(
            tmp_path,
            ready_event=ready_event,
            stop_event=stop_event,
            port=0,
            env=env,
        )

    server_thread = threading.Thread(target=_run, daemon=True)
    server_thread.start()

    try:
        assert ready_event.wait(timeout=10.0), "server failed to bind and become ready"
    finally:
        stop_event.set()
        server_thread.join(timeout=5.0)


def test_empty_inherited_fd_fails_closed(tmp_path: Path) -> None:
    """When MCP_INHERITED_FD_ENV is set but blank, the server fails closed.

    A parent bridge that promised an inherited listening FD and handed
    us a blank string has lost its endpoint contract: silently falling
    back to a fresh bind would reintroduce the ECONNREFUSED window
    R1 eliminates. The only safe behavior is a typed ``McpServerError``
    with ``last_stage="fd_inherit_failed"``. The absent-variable
    fallback (no env var at all) is preserved by a separate test.
    """
    env = _setup_session(tmp_path)
    env[MCP_INHERITED_FD_ENV] = ""

    with pytest.raises(McpServerError) as exc_info:
        run_standalone_server(tmp_path, env=env, port=0)

    err = exc_info.value
    assert err.last_stage == "fd_inherit_failed"
    assert read_last_stage(tmp_path) == "fd_inherit_failed"


def test_valid_inherited_fd_is_adopted_successfully(tmp_path: Path) -> None:
    """When MCP_INHERITED_FD_ENV is a live listening socket, adoption succeeds."""
    env = _setup_session(tmp_path)
    parent_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    parent_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    parent_sock.bind(("127.0.0.1", 0))
    parent_sock.listen(5)
    addr: tuple[str, int] = parent_sock.getsockname()[:2]
    host, port = addr

    env[MCP_INHERITED_FD_ENV] = str(parent_sock.fileno())

    ready_event = threading.Event()
    stop_event = threading.Event()

    def _run() -> None:
        run_standalone_server(
            tmp_path,
            host=host,
            port=port,
            ready_event=ready_event,
            stop_event=stop_event,
            env=env,
        )

    server_thread = threading.Thread(target=_run, daemon=True)
    server_thread.start()

    try:
        assert ready_event.wait(timeout=10.0), "server failed to adopt inherited socket"
    finally:
        stop_event.set()
        server_thread.join(timeout=5.0)
        with contextlib.suppress(OSError):
            parent_sock.close()


@pytest.mark.timeout_seconds(20)
@pytest.mark.subprocess_e2e
def test_real_subprocess_invalid_inherited_fd_fails_closed(tmp_path: Path) -> None:
    """A real child subprocess with invalid RALPH_MCP_INHERITED_FD fails closed."""
    spawn_id = str(uuid.uuid4())
    env = _setup_session(tmp_path)
    child_env = dict(os.environ)
    child_env.update(env)
    child_env[MCP_INHERITED_FD_ENV] = "99999"
    child_env[MCP_STAGE_ID_ENV] = spawn_id

    proc = get_process_manager().spawn(
        [
            sys.executable,
            "-m",
            "ralph.mcp.server",
            "--workspace",
            str(tmp_path),
            "--port",
            "0",
        ],
        SpawnOptions(cwd=str(tmp_path), env=child_env, label="mcp-invalid-fd-child"),
    )

    rc = proc.wait(timeout=15)
    assert rc != 0, f"child process should have failed, but exited with {rc}"
    recorded = read_last_stage(tmp_path, spawn_id=spawn_id)
    assert recorded == "fd_inherit_failed"


@pytest.mark.timeout_seconds(20)
@pytest.mark.subprocess_e2e
def test_real_subprocess_empty_inherited_fd_fails_closed(tmp_path: Path) -> None:
    """A real child subprocess with empty RALPH_MCP_INHERITED_FD fails closed end-to-end.

    Regression for D-01: a parent bridge that promised an inherited
    listening FD and handed the child a blank string must surface
    typed ``McpServerError`` (last_stage="fd_inherit_failed") rather
    than silently rebind and reintroduce the ECONNREFUSED window R1
    eliminates. The real subprocess path proves the typed error
    reaches the parent's pipe as a non-zero exit, matching the
    in-process contract asserted by ``test_empty_inherited_fd_fails_closed``.
    """
    spawn_id = str(uuid.uuid4())
    env = _setup_session(tmp_path)
    child_env = dict(os.environ)
    child_env.update(env)
    child_env[MCP_INHERITED_FD_ENV] = ""
    child_env[MCP_STAGE_ID_ENV] = spawn_id

    proc = get_process_manager().spawn(
        [
            sys.executable,
            "-m",
            "ralph.mcp.server",
            "--workspace",
            str(tmp_path),
            "--port",
            "0",
        ],
        SpawnOptions(cwd=str(tmp_path), env=child_env, label="mcp-empty-fd-child"),
    )

    rc = proc.wait(timeout=15)
    assert rc != 0, f"child process should have failed, but exited with {rc}"
    recorded = read_last_stage(tmp_path, spawn_id=spawn_id)
    assert recorded == "fd_inherit_failed"
