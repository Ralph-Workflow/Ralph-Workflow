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

import pytest

from ralph.mcp.protocol._startup_timeouts import (
    mcp_preflight_timeout_from_env,
    mcp_whole_startup_budget_from_env,
)
from ralph.mcp.protocol.capability_mapping import Capability
from ralph.mcp.protocol.env import (
    MCP_INHERITED_FD_ENV,
    MCP_PREFLIGHT_TIMEOUT_MS_ENV,
    MCP_RESPAWN_ENV,
    MCP_STAGE_ID_ENV,
    MCP_UPSTREAM_PROBE_TIMEOUT_MS_ENV,
)
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._start_stage_tracker import read_last_stage
from ralph.mcp.server.lifecycle import session_payload_json
from ralph.mcp.upstream.config import UPSTREAM_MCP_TOOL_CATALOG_ENV

_EXPECTED_CATALOG_PUBLISH_OVERRUN_EXIT_CODE: int = 34

pytestmark = pytest.mark.required_auto_integrate_e2e


def _setup_session(tmp_path: Path) -> dict[str, str]:
    session = AgentSession(
        session_id="test-session-catalog-publish",
        run_id="test-run-catalog-publish",
        drain="test",
        capabilities={cap.value for cap in Capability},
    )
    session_file = tmp_path / "session.json"
    session_file.write_text(session_payload_json(session), encoding="utf-8")
    return {
        "RALPH_MCP_SESSION_FILE": str(session_file),
    }


def _write_mcp_toml_with_unreachable_upstream(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "mcp.toml").write_text(
        '[mcp_servers.unreachable]\ntransport = "http"\nurl = "http://127.0.0.1:1/mcp"\n',
        encoding="utf-8",
    )


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
def test_real_child_catalog_publish_fifo_fails_closed_with_typed_exit(
    tmp_path: Path,
) -> None:
    spawn_id = str(uuid.uuid4())
    env = _setup_session(tmp_path)
    _write_mcp_toml_with_unreachable_upstream(tmp_path)

    catalog_dir = tmp_path / ".agent" / "mcp"
    catalog_dir.mkdir(parents=True, exist_ok=True)
    fifo_path = catalog_dir / "parent_catalog.json.tmp"
    os.mkfifo(fifo_path)

    parent_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    parent_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    parent_sock.bind(("127.0.0.1", 0))
    parent_sock.listen(128)
    addr: tuple[str, int] = parent_sock.getsockname()[:2]
    host, port = addr

    child_env = dict(os.environ)
    child_env.pop(UPSTREAM_MCP_TOOL_CATALOG_ENV, None)
    child_env.pop(MCP_RESPAWN_ENV, None)
    child_env.update(env)
    child_env[MCP_INHERITED_FD_ENV] = str(parent_sock.fileno())
    child_env[MCP_STAGE_ID_ENV] = spawn_id
    child_env[MCP_UPSTREAM_PROBE_TIMEOUT_MS_ENV] = "200"
    child_env["RALPH_MCP_STRICT"] = "0"
    child_env[MCP_PREFLIGHT_TIMEOUT_MS_ENV] = "4100"

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

        rc = proc.wait(timeout=15.0)

        assert rc == _EXPECTED_CATALOG_PUBLISH_OVERRUN_EXIT_CODE, (
            f"child must exit with _EXPECTED_CATALOG_PUBLISH_OVERRUN_EXIT_CODE="
            f"{_EXPECTED_CATALOG_PUBLISH_OVERRUN_EXIT_CODE}, "
            f"got {rc} (would have hung alive-but-unserving before the fix)"
        )

        stderr_data = _drain_pipe(proc.stderr, time.monotonic() + 1.0)
        stderr_text = stderr_data.decode("utf-8", errors="replace")
        assert "upstream_catalog_or_discovery_done" in stderr_text, (
            f"stderr must name last_stage='upstream_catalog_or_discovery_done': {stderr_text!r}"
        )
        assert "pre-serve overrun" in stderr_text, (
            f"stderr must contain the typed overrun error: {stderr_text!r}"
        )

        last_recorded = read_last_stage(tmp_path, spawn_id=spawn_id)
        assert last_recorded == "upstream_catalog_or_discovery_done", (
            f"expected last stage 'upstream_catalog_or_discovery_done', got {last_recorded!r}"
        )

        assert client_sock is not None
    finally:
        if client_sock is not None:
            with contextlib.suppress(OSError):
                client_sock.close()
        with contextlib.suppress(OSError):
            parent_sock.close()


def test_whole_startup_budget_is_strictly_below_preflight() -> None:
    whole_budget = mcp_whole_startup_budget_from_env()
    preflight_timeout = mcp_preflight_timeout_from_env().total_seconds()
    assert whole_budget > 0
    assert whole_budget < preflight_timeout
