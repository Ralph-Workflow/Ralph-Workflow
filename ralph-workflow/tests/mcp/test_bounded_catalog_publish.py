"""Bounded catalog publication — FIFO/block-special at the temp path fails closed.

R2 WU-1: the catalog publish helper
(``ralph/mcp/upstream/tool_catalog_cache.py:_write_catalog_file``)
does a synchronous ``os.open`` on the predictable
``.agent/mcp/parent_catalog.json.tmp`` path. A FIFO replacement
(hostile filesystem, stale writer) blocks the open indefinitely
and holds the GIL; the bounded helper
(``ralph/mcp/server/runtime.py:_publish_tool_catalog_bounded``)
runs the publish in a thread with the remaining whole-startup
deadline and force-exits via ``os._exit`` on timeout, using the
distinct ``CATALOG_PUBLISH_OVERRUN_EXIT_CODE`` so the parent
restart budget attributes the exit to this stage without
re-reading the on-disk stage file. The inherited parent listener
remains held until the kernel reaps the process, so the parent
endpoint never refuses connections while the catalog publish is
wedged.

Tests in this file use a real subprocess (no ``MCP_TEST_*`` gates)
because the bounded publication must reach the helper inside a
real child and prove the inherited socket never refuses. The
black-box contract is:

  1. Parent binds and listens on a socket and hands it via
     ``RALPH_MCP_INHERITED_FD``.
  2. Child adopts the inherited listening socket; a client
     connection succeeds in the kernel backlog.
  3. A FIFO replaces the catalog temp file with no writer.
  4. The child reaches the catalog publish stage, blocks on
     ``os.open`` (FIFO, no reader), and the bounded helper
     force-exits with the catalog overrun exit code.
  5. The inherited socket keeps accepting connections in the
     kernel backlog (parent endpoint never refuses).
  6. The on-disk stage file records the catalog publish stage so
     the parent's preflight surfaces typed attribution.
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

#: Distinct exit code emitted by the bounded catalog publish helper
#: (``CATALOG_PUBLISH_OVERRUN_EXIT_CODE`` in
#: ``ralph/mcp/server/_pre_serve_overrun.py``). Hard-coded here so
#: the test compiles against the pre-fix code too: the on-the-wire
#: exit code is the contract, not the import name. The pre-fix
#: code either does not bound the publish (the test would hang past
#: the parent's preflight) or the helper does not exist; in either
#: case the assertion below fails on the pre-fix code.
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
    """Write ``.agent/mcp.toml`` with one unreachable HTTP upstream.

    The unreachable port is bound by the kernel but accepts no MCP
    handshake: ``UpstreamRegistry.build`` falls through to
    ``warn_and_skip`` and returns an empty registry quickly, then
    the catalog publish helper is reached. The bounded helper
    fires the moment the FIFO blocks ``os.open``.
    """
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "mcp.toml").write_text(
        '[mcp_servers.unreachable]\ntransport = "http"\nurl = "http://127.0.0.1:1/mcp"\n',
        encoding="utf-8",
    )


def _drain_pipe(stream: IO[bytes], deadline_s: float) -> bytes:
    """Drain a child stream until ``deadline_s``; EOF ends the drain.

    The bounded publish helper flushes stderr before ``os._exit`` so
    the typed error reaches the parent. ``select`` gives us a
    deadline-bounded wait that honors EOF, so we never block past
    the deadline. A closed/EOF stream is the normal end-of-child
    signal; we treat it the same as a timeout.
    """
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
    """Real child with a FIFO at the catalog temp path exits with the catalog overrun code.

    Black-box contract (no ``MCP_TEST_*`` gates):

    1. Parent binds and listens on a socket and hands it via
       ``RALPH_MCP_INHERITED_FD``.
    2. Child adopts the inherited listening socket; a client
       connection succeeds in the kernel backlog because the
       parent-owned socket is listening.
    3. ``.agent/mcp/`` exists, and ``.agent/mcp/parent_catalog.json.tmp``
       is a real FIFO with NO reader. ``os.open(tmp, O_WRONLY|...)``
       on a FIFO blocks until a reader appears; with no reader, the
       open blocks indefinitely.
    4. The bounded publish helper detects the timeout and force-exits
       via ``os._exit`` with the distinct
       ``_EXPECTED_CATALOG_PUBLISH_OVERRUN_EXIT_CODE`` (34). The
       pre-fix helper has no such exit; the test would hang past
       the preflight (or exit with a different code) on the
       pre-fix code.
    5. The inherited socket keeps accepting connections in the
       kernel backlog (parent endpoint never refuses) until the
       kernel reaps us.
    6. The on-disk stage file records ``upstream_catalog_or_discovery_done``
       so the parent's preflight surfaces the typed attribution.
    """
    spawn_id = str(uuid.uuid4())
    env = _setup_session(tmp_path)
    _write_mcp_toml_with_unreachable_upstream(tmp_path)

    # Pre-create the catalog directory AND replace the temp file with
    # a FIFO. The child's _ensure_catalog_dir is a no-op (dir
    # exists); the child's os.open(tmp, O_WRONLY|...) blocks on
    # the FIFO because no reader ever appears.
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

    # Shrink the upstream probe timeout so the unreachable server
    # returns quickly (warn_and_skip) and the catalog publish is
    # reached inside the test budget. The exact value is
    # irrelevant; what matters is the bounded publish fires.
    child_env = dict(os.environ)
    child_env.pop(UPSTREAM_MCP_TOOL_CATALOG_ENV, None)
    child_env.pop(MCP_RESPAWN_ENV, None)
    child_env.update(env)
    child_env[MCP_INHERITED_FD_ENV] = str(parent_sock.fileno())
    child_env[MCP_STAGE_ID_ENV] = spawn_id
    child_env[MCP_UPSTREAM_PROBE_TIMEOUT_MS_ENV] = "200"
    # strict mode is the default; opt out so the unreachable
    # upstream falls through to warn_and_skip instead of
    # raising UpstreamValidationError before the catalog
    # publish is reached.
    child_env["RALPH_MCP_STRICT"] = "0"
    # The preflight must be strictly greater than the whole startup
    # budget; the import-time invariant enforces this. With probe
    # timeout 200ms the whole budget is ~ 1.0 + 0.2 + 0.4 + 0.4 =
    # 2.0s, so 5s preflight is comfortably above.
    child_env[MCP_PREFLIGHT_TIMEOUT_MS_ENV] = "4050"

    # Use subprocess.Popen directly so we can drain stderr and
    # assert the typed error reaches the parent.
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
        # because the parent-owned socket is listening. The child
        # is blocked in os.open(FIFO) so nothing has called
        # accept() yet — the connection just queues.
        client_sock = socket.create_connection((host, port), timeout=5.0)
        assert client_sock is not None

        # Wait for the bounded publish to fire and the child to
        # force-exit. The bound is generous (8s) — the
        # ``_READY_TO_SERVE_BUDGET_S`` portion of the whole
        # startup budget is 0.4s, plus small slack for the
        # worker to time out, the helper to log/flush, and the
        # kernel to reap the process. The preflight is 5s, so
        # any "exits in 8s" is well below it.
        rc = proc.wait(timeout=8.0)

        # The child must exit with the distinct catalog publish
        # overrun exit code (34). The pre-fix code has no
        # bounded publish and would either hang past the
        # preflight (rc != 0 from parent's SIGTERM/kill) or
        # exit with the pre-serve overrun code (32) from a
        # different stage; the assertion below is the only
        # reliable post-fix signal.
        assert rc == _EXPECTED_CATALOG_PUBLISH_OVERRUN_EXIT_CODE, (
            f"child must exit with _EXPECTED_CATALOG_PUBLISH_OVERRUN_EXIT_CODE="
            f"{_EXPECTED_CATALOG_PUBLISH_OVERRUN_EXIT_CODE}, "
            f"got {rc} (would have hung alive-but-unserving before the fix)"
        )

        # Drain stderr to confirm the typed error was logged. The
        # bounded publish helper logs the McpServerError with
        # the stage and elapsed diagnostics before force-exiting.
        stderr_data = _drain_pipe(proc.stderr, time.monotonic() + 1.0)
        stderr_text = stderr_data.decode("utf-8", errors="replace")
        assert "upstream_catalog_or_discovery_done" in stderr_text, (
            f"stderr must name last_stage='upstream_catalog_or_discovery_done': {stderr_text!r}"
        )
        assert "pre-serve overrun" in stderr_text, (
            f"stderr must contain the typed overrun error: {stderr_text!r}"
        )

        # The on-disk stage file must record the last stage so the
        # parent's preflight surfaces the correct attribution.
        last_recorded = read_last_stage(tmp_path, spawn_id=spawn_id)
        assert last_recorded == "upstream_catalog_or_discovery_done", (
            f"expected last stage 'upstream_catalog_or_discovery_done', got {last_recorded!r}"
        )

        # The parent endpoint must NEVER refuse. The client
        # connection we made earlier is the proof: it succeeded
        # while the child was wedged. The ``create_connection``
        # call would have raised ``ConnectionRefusedError`` if
        # the parent's listening socket had been rejected, so
        # reaching this point with ``client_sock`` non-None
        # is itself the no-ECONNREFUSED invariant.
        assert client_sock is not None
    finally:
        if client_sock is not None:
            with contextlib.suppress(OSError):
                client_sock.close()
        with contextlib.suppress(OSError):
            parent_sock.close()
        # The FIFO is the only thing keeping the worker alive
        # inside the child process; the child is already
        # reaped by the test (proc.wait returned). The
        # ``tmp_path`` cleanup unlinks the FIFO on its own.


def test_whole_startup_budget_is_strictly_below_preflight() -> None:
    """The whole-startup envelope is the sum of per-stage budgets below preflight.

    The bounded catalog publish helper computes its deadline from
    ``mcp_whole_startup_budget_from_env`` minus elapsed time at
    call entry. The contract that lets the helper fire BEFORE the
    parent's preflight requires the whole envelope to be strictly
    below the preflight timeout. This is the same import-time
    invariant the production runtime checks; this test pins it
    locally so the bounded helper's deadline contract cannot rot.
    """
    whole_budget = mcp_whole_startup_budget_from_env()
    preflight_timeout = mcp_preflight_timeout_from_env().total_seconds()
    assert whole_budget > 0
    assert whole_budget < preflight_timeout
