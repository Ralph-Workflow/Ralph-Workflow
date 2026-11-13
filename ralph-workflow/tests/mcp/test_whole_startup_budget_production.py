from __future__ import annotations

import contextlib
import os
import select
import socket
import subprocess
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import IO, cast
from unittest.mock import patch

import pytest

from ralph.mcp.protocol._startup_timeouts import (
    _PROBE_SAFETY_S,
    _check_cumulative_startup_deadline,
    _check_real_startup_deadline,
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
from ralph.process.manager import ProcessTerminationIssuer, SpawnOptions, get_process_manager

pytestmark = pytest.mark.required_auto_integrate_e2e


def test_real_deadline_is_preflight_minus_safety() -> None:
    real_deadline = mcp_preflight_timeout_from_env({}) - timedelta(seconds=_PROBE_SAFETY_S)
    assert real_deadline.total_seconds() == 28.0

    with patch("ralph.mcp.protocol._startup_timeouts._time_module.monotonic", return_value=100.0):
        t0_27_5s = 100.0 - 27.5
        _check_real_startup_deadline("stage_27_5", t0_27_5s, {})

        t0_28_5s = 100.0 - 28.5
        with pytest.raises(McpServerError) as exc_info:
            _check_real_startup_deadline("stage_28_5", t0_28_5s, {})

        err = exc_info.value
        assert err.last_stage == "stage_28_5"
        assert err.elapsed_s == 28.5


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
    whole_budget = mcp_whole_startup_budget_from_env()
    preflight_timeout = mcp_preflight_timeout_from_env().total_seconds()
    assert whole_budget < preflight_timeout


def test_cumulative_deadline_enforces_at_session_ready(tmp_path: Path) -> None:
    env = _setup_session(tmp_path)
    whole_budget = mcp_whole_startup_budget_from_env(env)
    past_budget = whole_budget + 1.0

    ticks = iter((0.0, 0.0, past_budget, past_budget * 2, past_budget * 3))
    with (
        patch.object(runtime_module, "_clock", lambda: next(ticks)),
        pytest.raises(McpServerError) as exc_info,
    ):
        run_standalone_server(tmp_path, env=env, port=0)

    err = exc_info.value
    assert err.last_stage in ("env_loaded", "session_ready")
    assert err.elapsed_s is not None and err.elapsed_s >= whole_budget


def test_cumulative_deadline_enforces_at_config_loading(tmp_path: Path) -> None:
    whole_budget = mcp_whole_startup_budget_from_env({})
    past_budget = whole_budget + 1.0

    ticks = iter((0.0, 0.0, past_budget, past_budget * 2, past_budget * 3))
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
    env = _setup_session(tmp_path)
    preflight = mcp_preflight_timeout_from_env(env).total_seconds()
    real_deadline = preflight - _PROBE_SAFETY_S

    expired_t0 = time.monotonic() - real_deadline - 1.0
    with pytest.raises(McpServerError) as exc_info:
        _check_cumulative_startup_deadline("ready_to_serve", expired_t0, env)

    err = exc_info.value
    assert err.last_stage == "ready_to_serve"
    assert err.elapsed_s is not None and err.elapsed_s > real_deadline


@pytest.mark.timeout_seconds(20)
@pytest.mark.subprocess_e2e
def test_black_box_real_child_slowed_past_budget_fails_preflight(tmp_path: Path) -> None:
    spawn_id = str(uuid.uuid4())
    sentinel = tmp_path / "pre_serve_gate"
    env = _setup_session(tmp_path)

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
    child_env[MCP_PREFLIGHT_TIMEOUT_MS_ENV] = "4300"
    real_deadline_s = mcp_preflight_timeout_from_env(child_env).total_seconds() - _PROBE_SAFETY_S

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
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            stage = read_last_stage(tmp_path, spawn_id=spawn_id)
            if stage == "registry_built":
                break
            time.sleep(0.05)

        assert read_last_stage(tmp_path, spawn_id=spawn_id) == "registry_built"

        client_sock = socket.create_connection((host, port), timeout=5.0)
        assert client_sock is not None

        time.sleep(real_deadline_s + 0.2)

        sentinel.touch()

        rc = proc.wait(timeout=10.0)
        assert rc != 0, f"child should have failed with McpServerError, but exited with {rc}"

        last_recorded = read_last_stage(tmp_path, spawn_id=spawn_id)
        assert last_recorded == "registry_built"
    finally:
        if client_sock is not None:
            with contextlib.suppress(OSError):
                client_sock.close()
        with contextlib.suppress(OSError):
            parent_sock.close()
        with contextlib.suppress(Exception):
            proc.kill(issuer=ProcessTerminationIssuer.OPERATOR)


def _drain_pipe(stream: IO[bytes], deadline_s: float) -> bytes:
    chunks: list[bytes] = []
    fd = stream.fileno()
    while time.monotonic() < deadline_s:
        remaining = deadline_s - time.monotonic()
        if remaining <= 0:
            break
        try:
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
    spawn_id = str(uuid.uuid4())

    session_file = tmp_path / "session.json"
    os.mkfifo(session_file)

    parent_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    parent_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    parent_sock.bind(("127.0.0.1", 0))
    parent_sock.listen(128)
    addr: tuple[str, int] = parent_sock.getsockname()[:2]
    host, port = addr

    pre_serve_budget_ms = "300"
    preflight_timeout_ms = "4100"

    child_env = dict(os.environ)
    child_env["RALPH_MCP_SESSION_FILE"] = str(session_file)
    child_env["RALPH_MCP_PRE_SERVE_TIMEOUT_MS"] = pre_serve_budget_ms
    child_env["RALPH_MCP_PREFLIGHT_TIMEOUT_MS"] = preflight_timeout_ms
    child_env[MCP_INHERITED_FD_ENV] = str(parent_sock.fileno())
    child_env[MCP_STAGE_ID_ENV] = spawn_id
    child_env.pop(MCP_TEST_PRE_SERVE_GATE_ENV, None)

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

        client_sock = socket.create_connection((host, port), timeout=5.0)
        assert client_sock is not None

        rc = proc.wait(timeout=10.0)

        assert rc == _EXPECTED_SESSION_OVERRUN_EXIT_CODE, (
            f"child must exit with _EXPECTED_SESSION_OVERRUN_EXIT_CODE="
            f"{_EXPECTED_SESSION_OVERRUN_EXIT_CODE}, "
            f"got {rc} (would have hung alive-but-unserving before the fix)"
        )

        stderr_data = _drain_pipe(proc.stderr, time.monotonic() + 1.0)
        stderr_text = stderr_data.decode("utf-8", errors="replace")
        assert "session_ready" in stderr_text, (
            f"stderr must name last_stage='session_ready': {stderr_text!r}"
        )
        assert "pre-serve overrun" in stderr_text, (
            f"stderr must contain the typed overrun error: {stderr_text!r}"
        )

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
