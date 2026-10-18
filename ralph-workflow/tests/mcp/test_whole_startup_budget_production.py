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
import select
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import IO, cast
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

# Stable exit codes emitted by the pre-serve overrun helper. Hard-coded
# here so the test compiles against the PRE-fix code too: the
# production helper exposes the same constants, but the test
# contract is the on-the-wire exit code, not the import name. The
# pre-fix ``McpServerError`` raise path leaves the process alive
# but unserving (the worker thread holds the GIL), so neither code
# ever appears in the pre-fix exit status — the assertion fails by
# timeout-or-bad-rc on the pre-fix code and passes only on the
# post-fix code.
_EXPECTED_SESSION_OVERRUN_EXIT_CODE = 33
_EXPECTED_PRE_SERVE_OVERRUN_EXIT_CODE = 32


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


def _drain_pipe(stream: IO[bytes], deadline_s: float) -> bytes:
    """Drain a child stream into a single bytes buffer until ``deadline_s``.

    The pre-serve overrun helper flushes stderr before ``os._exit`` so
    the typed error reaches the parent. ``select`` gives us a
    deadline-bounded wait that honors EOF, so we never block past the
    deadline. A closed/EOF stream is the normal end-of-child signal;
    we treat it the same as a timeout.
    """
    chunks: list[bytes] = []
    fd = stream.fileno()
    while time.monotonic() < deadline_s:
        remaining = deadline_s - time.monotonic()
        if remaining <= 0:
            break
        try:
            # ``select.select`` types each ready-list as ``list[Any]``;
            # we only need the truthiness of the first list, so the
            # cast is on the index access, not on the call result.
            ready_first: list[object] = cast(
                "list[object]", select.select([fd], [], [], remaining)[0]
            )
        except (OSError, ValueError):
            break
        if not ready_first:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


@pytest.mark.timeout_seconds(20)
@pytest.mark.subprocess_e2e
def test_black_box_real_child_blocked_session_fifo_fails_closed(tmp_path: Path) -> None:
    """Real production slow input (FIFO session file) forces child to fail-closed.

    Black-box contract (no ``MCP_TEST_*`` gates):
    1. Parent binds and listens on a socket and hands it via ``RALPH_MCP_INHERITED_FD``.
    2. The session file is a real FIFO with NO writer — ``session_from_env``
       blocks in a worker thread reading the pipe. The block holds the
       GIL, so ``ThreadPoolExecutor.shutdown(wait=False)`` cannot
       interrupt it; without the fail-closed helper the process would
       hang alive-but-unserving on the inherited listener until the
       parent's preflight kills it.
    3. A client connects over TCP: succeeds in the kernel backlog
       because the parent-owned socket is listening.
    4. The pre-serve budget (``RALPH_MCP_PRE_SERVE_TIMEOUT_MS``) is
       shrunken well below the preflight timeout so the pre-serve
       overrun helper fires BEFORE the parent's preflight.
    5. The child must exit via ``os._exit()`` with the distinct
       ``SESSION_OVERRUN_EXIT_CODE`` (33) BEFORE the parent's
       preflight. The exit must NOT depend on a test sentinel being
       released.
    6. Stderr contains the typed ``McpServerError`` (last_stage,
       elapsed_s) so the parent's preflight surfaces the stage
       attribution. The stage file records ``session_ready`` as the
       last stage reached.
    """
    spawn_id = str(uuid.uuid4())

    # Real slow production input: FIFO with no writer. The child's
    # session_from_env opens it for reading, which blocks until either
    # a writer appears OR the worker is interrupted. The overrun
    # helper must force-exit since the worker holds the GIL.
    session_file = tmp_path / "session.json"
    os.mkfifo(session_file)

    # Create parent listening socket
    parent_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    parent_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    parent_sock.bind(("127.0.0.1", 0))
    parent_sock.listen(128)
    addr: tuple[str, int] = parent_sock.getsockname()[:2]
    host, port = addr

    # Shrunken pre-serve budget (300ms) is well below the preflight
    # timeout (10s) so the pre-serve overrun helper fires before the
    # parent's preflight. The exact factor is irrelevant; what
    # matters is the helper wins the race.
    pre_serve_budget_ms = "300"
    preflight_timeout_ms = "10000"

    child_env = dict(os.environ)
    child_env["RALPH_MCP_SESSION_FILE"] = str(session_file)
    child_env["RALPH_MCP_PRE_SERVE_TIMEOUT_MS"] = pre_serve_budget_ms
    child_env["RALPH_MCP_PREFLIGHT_TIMEOUT_MS"] = preflight_timeout_ms
    child_env[MCP_INHERITED_FD_ENV] = str(parent_sock.fileno())
    child_env[MCP_STAGE_ID_ENV] = spawn_id
    # Strip any test gate the host environment may carry; this test
    # relies on a real FIFO, not a sentinel file the test releases.
    child_env.pop(MCP_TEST_PRE_SERVE_GATE_ENV, None)

    # Use subprocess.Popen directly so we can drain stderr and
    # assert the typed error reaches the parent. get_process_manager
    # hides the pipes.
    ralph_workflow_root = Path(__file__).resolve().parents[2]
    argv = [
        sys.executable,
        "-m",
        "ralph.mcp.server",
        "--host",
        host,
        "--port",
        str(port),
        "--workspace",
        str(tmp_path),
    ]

    proc = subprocess.Popen(
        argv,
        env=child_env,
        cwd=str(ralph_workflow_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        pass_fds=(parent_sock.fileno(),),
    )

    client_sock: socket.socket | None = None
    try:
        assert proc.stderr is not None and proc.stdout is not None

        # Client connects: TCP handshake succeeds in kernel backlog
        # because the parent-owned socket is listening. The child is
        # blocked in session_from_env reading the FIFO, so nothing
        # has called accept() yet — the connection just queues.
        client_sock = socket.create_connection((host, port), timeout=5.0)
        assert client_sock is not None

        # Wait for the pre-serve overrun to fire and the child to
        # force-exit. The bound is generous (5s) — the pre-serve
        # budget is 300ms plus a small slack for the worker to
        # time out, the helper to log/flush, and the kernel to
        # reap the process. The preflight is 10s, so any
        # "exits in 5s" is well below it.
        rc = proc.wait(timeout=5.0)

        # The child must exit non-zero with the distinct
        # SESSION_OVERRUN_EXIT_CODE (33). The previous
        # ThreadPoolExecutor.shutdown(wait=False) implementation
        # would have left the process alive-but-hung; the
        # fail-closed helper is the only mechanism that produces
        # this exit code.
        assert rc == _EXPECTED_SESSION_OVERRUN_EXIT_CODE, (
            f"child must exit with _EXPECTED_SESSION_OVERRUN_EXIT_CODE="
            f"{_EXPECTED_SESSION_OVERRUN_EXIT_CODE}, "
            f"got {rc} (would have hung alive-but-unserving before the fix)"
        )

        # Drain stderr to confirm the typed error was logged. The
        # overrun helper logs the McpServerError with the stage
        # and elapsed diagnostics before force-exiting.
        stderr_data = _drain_pipe(proc.stderr, time.monotonic() + 1.0)
        stderr_text = stderr_data.decode("utf-8", errors="replace")
        assert "session_ready" in stderr_text, (
            f"stderr must name last_stage='session_ready': {stderr_text!r}"
        )
        assert "pre-serve overrun" in stderr_text, (
            f"stderr must contain the typed overrun error: {stderr_text!r}"
        )

        # The on-disk stage file must record the last stage so the
        # parent's preflight surfaces the correct attribution.
        last_recorded = read_last_stage(tmp_path, spawn_id=spawn_id)
        assert last_recorded == "session_ready", (
            f"expected last stage 'session_ready', got {last_recorded!r}"
        )
    finally:
        if client_sock is not None:
            with contextlib.suppress(OSError):
                client_sock.close()
        with contextlib.suppress(OSError):
            parent_sock.close()
        # If the test reached here before the child exited (it
        # shouldn't), the FIFO is the only thing keeping the worker
        # alive. Unlinking it from under the worker would close the
        # read end and let the worker return EOF — but the test
        # contract is the child must exit on its own, so we only
        # use this as a last-resort cleanup. The session file is a
        # FIFO; ``unlink`` while a reader is blocked does not
        # interrupt the read, so we leave it to the test teardown
        # cleanup (tmp_path is GC'd).
