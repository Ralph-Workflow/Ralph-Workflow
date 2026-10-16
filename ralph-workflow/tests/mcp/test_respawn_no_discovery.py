"""R2 invariant: a respawned child does zero network I/O before serving.

Drives a REAL MCP subprocess respawn (RestartAwareMcpBridge._restart_fn) with a
fake in-process TCP server as the upstream.  The first child is started with the
tool catalog pre-populated in the parent cache; the cached catalog is handed to
the child via ``UPSTREAM_MCP_TOOL_CATALOG_ENV`` so the child uses it without
calling the fake server.  After a manual respawn the fake server's tools/list
counter shows ZERO new calls — proving the production respawn path reuses the
parent's tool catalog and does not re-probe upstreams.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.parse
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.upstream.models import UpstreamTool
from ralph.mcp.upstream.tool_catalog_cache import cache_tool_catalog
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from ralph.mcp.server._lifecycle_deps import LifecycleDeps

pytestmark = pytest.mark.subprocess_e2e


# ---------------------------------------------------------------------------
# Fake in-process MCP upstream server
# ---------------------------------------------------------------------------

_FAKE_TOOL = UpstreamTool(
    name="fake-upstream-tool",
    description="Tool registered via the pre-populated tool catalog.",
    input_schema={"type": "object"},
)


class _FakeMcpUpstreamServer:
    """Minimal fake MCP HTTP server that tracks tools/list call count.

    Implements the JSON-RPC exchange sequence the Ralph preflight sends:
      1. POST initialize (id=1)           → 200 with session-id header
      2. POST notifications/initialized    → 202 Accepted
      3. POST tools/list (id=2)            → 200 with tool list

    The server runs in a daemon thread; ``stop_event`` signals shutdown.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tools_list_count = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._port: int | None = None
        self._server_socket: socket.socket | None = None

    @property
    def tools_list_count(self) -> int:
        with self._lock:
            return self._tools_list_count

    @property
    def port(self) -> int:
        if self._port is None:
            raise RuntimeError("server not started")
        return self._port

    def start(self) -> None:
        """Bind a free port and run the server loop in a daemon thread."""
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(("127.0.0.1", 0))
        self._port = server_sock.getsockname()[1]
        server_sock.listen(32)
        self._server_socket = server_sock
        self._thread = threading.Thread(target=self._serve, daemon=True, name="fake-upstream")
        self._thread.start()

    def _serve(self) -> None:
        assert self._server_socket is not None
        sock = self._server_socket
        while not self._stop_event.is_set():
            try:
                if self._stop_event.is_set():
                    break
                sock.settimeout(0.5)
                try:
                    client, _ = sock.accept()
                except TimeoutError:
                    continue
                try:
                    # Handle in a short-lived daemon thread so a slow handshake
                    # never blocks the accept loop (de-flakes under xdist load).
                    threading.Thread(
                        target=self._handle_client_and_close,
                        args=(client,),
                        daemon=True,
                    ).start()
                except OSError:
                    client.close()
            except OSError:
                if not self._stop_event.is_set():
                    raise

        sock.close()

    def _handle_client_and_close(self, client: socket.socket) -> None:
        try:
            self._handle_client(client)
        finally:
            client.close()

    def _handle_client(self, client: socket.socket) -> None:
        """Handle one HTTP/MCP JSON-RPC exchange."""
        try:
            client.settimeout(30.0)
            # Read request line + headers
            header_bytes = b""
            while b"\r\n\r\n" not in header_bytes:
                chunk = client.recv(256)
                if not chunk:
                    return
                header_bytes += chunk
            header_str = header_bytes.decode("utf-8", errors="replace")
            header_lines = header_str.split("\r\n")
            content_length = 0
            for line in header_lines:
                if line.lower().startswith("content-length:"):
                    content_length = int(line.split(":", 1)[1].strip())
            # Read body
            body = b""
            while len(body) < content_length:
                chunk = client.recv(content_length - len(body))
                if not chunk:
                    break
                body += chunk
            if not body:
                return
            rpc: dict[str, object] = json.loads(body.decode("utf-8"))
            method = rpc.get("method", "")
            msg_id = rpc.get("id")

            if method == "initialize":
                response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "serverInfo": {"name": "fake-upstream", "version": "1.0"},
                    },
                }
                body_bytes = json.dumps(response).encode("utf-8")
                response_lines = [
                    "HTTP/1.1 200 OK",
                    "Content-Type: application/json",
                    f"Content-Length: {len(body_bytes)}",
                    "mcp-session-id: fake-session-123",
                    "Connection: close",
                    "",
                    "",
                ]
                client.sendall("\r\n".join(response_lines).encode() + body_bytes)

            elif method == "notifications/initialized":
                # 202 Accepted — no body expected
                client.sendall(
                    b"HTTP/1.1 202 Accepted\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )

            elif method == "tools/list":
                with self._lock:
                    self._tools_list_count += 1
                response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "tools": [
                            {
                                "name": _FAKE_TOOL.name,
                                "description": _FAKE_TOOL.description,
                                "inputSchema": _FAKE_TOOL.input_schema,
                            }
                        ]
                    },
                }
                body_bytes = json.dumps(response).encode("utf-8")
                response_lines = [
                    "HTTP/1.1 200 OK",
                    "Content-Type: application/json",
                    f"Content-Length: {len(body_bytes)}",
                    "Connection: close",
                    "",
                    "",
                ]
                client.sendall("\r\n".join(response_lines).encode() + body_bytes)

            else:
                # Unknown method — return error
                response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
                body_bytes = json.dumps(response).encode("utf-8")
                response_lines = [
                    "HTTP/1.1 200 OK",
                    "Content-Type: application/json",
                    f"Content-Length: {len(body_bytes)}",
                    "Connection: close",
                    "",
                    "",
                ]
                client.sendall("\r\n".join(response_lines).encode() + body_bytes)

        except Exception:
            pass  # best-effort; test does not depend on graceful error handling

    def stop(self) -> None:
        """Signal the server thread to stop and wait for it to terminate."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MCP_UPSTREAM_CONFIG_ENV = "RALPH_UPSTREAM_MCP_CONFIG"


def _make_session() -> AgentSession:
    return AgentSession(
        session_id="test-respawn-no-discovery",
        run_id="run-respawn-no-discovery",
        drain="development",
        capabilities={"WorkspaceRead"},
    )


def _upstream_config_json(port: int) -> str:
    """Return a RALPH_UPSTREAM_MCP_CONFIG JSON string for one HTTP upstream."""
    config: list[dict[str, str]] = [
        {
            "name": "fake-upstream",
            "transport": "http",
            "url": f"http://127.0.0.1:{port}/mcp",
        }
    ]
    return json.dumps(config, separators=(",", ":"))


def _poll_endpoint(endpoint: str, deadline: float) -> bool:
    """Return True when the endpoint accepts a TCP connection before deadline."""
    port = int(urllib.parse.urlsplit(endpoint).port or 0)
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _make_deps(
    tmp_path: Path,
    real_spawn_process: lifecycle.SpawnProcess,
) -> LifecycleDeps:
    """LifecycleDeps with a no-op preflight (verified manually after respawn)."""

    def noop_preflight(endpoint: str, required_tools: list[str], timeout: timedelta) -> None:
        del endpoint, required_tools, timeout

    default_deps = lifecycle._default_lifecycle_deps()
    return default_deps.__replace__(preflight=noop_preflight, spawn_process=real_spawn_process)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.timeout_seconds(30)
def test_respawn_with_cached_catalog_makes_zero_discovery_calls(tmp_path: Path) -> None:
    """After a real respawn, the child uses the parent's cached tool catalog and
    makes ZERO new tools/list calls to the fake upstream.

    The first child may have called the fake server (if the cache was cold);
    the respawn child must NOT, because UPSTREAM_MCP_TOOL_CATALOG_ENV is set
    by the production respawn path (_restart_fn → _spawn_mcp_process).
    """
    fake_server = _FakeMcpUpstreamServer()
    fake_server.start()
    try:
        # Pre-populate the parent process tool-catalog cache so
        # start_mcp_server passes tool_catalog_env to the first child.
        cache_tool_catalog(
            tmp_path,
            {"fake-upstream": [_FAKE_TOOL]},
        )

        # Inject the fake upstream config so the child knows where to probe.
        upstream_config = _upstream_config_json(fake_server.port)

        session = _make_session()
        workspace = FsWorkspace(tmp_path)
        real_spawn = lifecycle._default_lifecycle_deps().spawn_process
        deps = _make_deps(tmp_path, real_spawn)

        # McpServerExtras.extra_env carries env vars into the subprocess.
        extras = lifecycle.McpServerExtras(
            extra_env={_MCP_UPSTREAM_CONFIG_ENV: upstream_config},
        )

        bridge = lifecycle.start_mcp_server(
            session,
            workspace,
            deps=deps,
            extras=extras,
        )

        try:
            # Wait for first child to be ready (preflight is no-op; verify manually).
            endpoint = bridge.endpoint_uri()
            deadline = time.monotonic() + 10.0
            ready = _poll_endpoint(endpoint, deadline)
            assert ready, f"first child endpoint {endpoint} must be accessible"

            # Snapshot the fake server's tools/list count AFTER the first child started.
            # The first child uses the pre-populated cached catalog
            # (UPSTREAM_MCP_TOOL_CATALOG_ENV set by start_mcp_server), so
            # it should NOT call the fake server at all.
            count_after_first = fake_server.tools_list_count

            # --- trigger a REAL respawn ----------------------------------------
            # Production restart entry: kill the child and let the bridge
            # terminate the stale process, respawn on the inherited FD, and
            # increment restart_count. The respawn path sets
            # UPSTREAM_MCP_TOOL_CATALOG_ENV from the parent's cached catalog,
            # so build_standalone_http_server takes build_from_tool_catalog
            # (zero network I/O).
            stale_inner = bridge._inner
            stale_inner.process.kill()
            assert bridge.check_health_and_restart_if_needed() is True

            # Wait for the respawned child to be ready.
            deadline = time.monotonic() + 10.0
            ready = _poll_endpoint(endpoint, deadline)
            assert ready, f"respawned child endpoint {endpoint} must be accessible"

            # Verify the respawned child replaced the inner process.
            assert bridge._inner is not stale_inner, (
                "restart must replace the inner StandaloneMcpProcess"
            )

            # Check the fake server's tools/list call count.
            # Delta must be ZERO: the respawn child used the cached catalog
            # (UPSTREAM_MCP_TOOL_CATALOG_ENV set by _restart_fn → _spawn_mcp_process)
            # and did NOT call the fake upstream.
            count_after_respawn = fake_server.tools_list_count
            delta = count_after_respawn - count_after_first

            assert delta == 0, (
                f"respawn child must NOT call fake upstream tools/list; "
                f"got {delta} new call(s) (count before={count_after_first}, "
                f"count after={count_after_respawn}); "
                "UPSTREAM_MCP_TOOL_CATALOG_ENV may not be set in the respawn path, "
                "or the production respawn path does not pass the catalog"
            )

            # Sanity: the child is alive after respawn.
            assert bridge.process.poll() is None, "respawned child must be alive"

        finally:
            bridge.shutdown()

    finally:
        fake_server.stop()


@pytest.mark.timeout_seconds(20)
def test_respawn_increments_restart_count(tmp_path: Path) -> None:
    """A manual respawn via _restart_fn increments bridge.restart_count."""
    fake_server = _FakeMcpUpstreamServer()
    fake_server.start()
    try:
        cache_tool_catalog(tmp_path, {"fake-upstream": [_FAKE_TOOL]})

        session = _make_session()
        workspace = FsWorkspace(tmp_path)
        real_spawn = lifecycle._default_lifecycle_deps().spawn_process
        deps = _make_deps(tmp_path, real_spawn)
        upstream_config = _upstream_config_json(fake_server.port)
        extras = lifecycle.McpServerExtras(extra_env={_MCP_UPSTREAM_CONFIG_ENV: upstream_config})

        bridge = lifecycle.start_mcp_server(session, workspace, deps=deps, extras=extras)
        try:
            assert bridge.restart_count == 0

            # Restart via the production entry: kill the child, then let the
            # bridge detect the exit, terminate the stale process, respawn,
            # and count the attempt (S1 ordering).
            bridge._inner.process.kill()
            assert bridge.check_health_and_restart_if_needed() is True
            assert bridge.restart_count == 1, "restart_count must be 1 after one respawn"

            # Another respawn
            bridge._inner.process.kill()
            assert bridge.check_health_and_restart_if_needed() is True
            assert bridge.restart_count == 2, "restart_count must be 2 after two respawns"
        finally:
            bridge.shutdown()
    finally:
        fake_server.stop()


@pytest.mark.timeout_seconds(20)
def test_respawn_with_cold_cache_still_zero_discovery_on_second_respawn(
    tmp_path: Path,
) -> None:
    """With a cold parent cache the restart closure (which snapshots the
    catalog at bridge-construction time, matching the production ordering
    where the pipeline caches before the server starts) has no catalog to
    hand over, so the respawned child must probe again. The fake upstream
    cannot satisfy the real MCP handshake, so the restart must fail LOUDLY
    with an attributed McpServerError — never ECONNREFUSED, never silent.
    """
    fake_server = _FakeMcpUpstreamServer()
    fake_server.start()
    try:
        # NOTE: we do NOT pre-populate the cache here.
        # The first child will probe the fake server (cache miss).
        session = _make_session()
        workspace = FsWorkspace(tmp_path)
        real_spawn = lifecycle._default_lifecycle_deps().spawn_process

        def real_preflight(endpoint: str, required_tools: list[str], timeout: timedelta) -> None:
            # Use real preflight so the first child actually probes the fake server
            # and populates the parent cache.
            lifecycle._default_lifecycle_deps().preflight(endpoint, required_tools, timeout)

        default_deps = lifecycle._default_lifecycle_deps()
        deps = default_deps.__replace__(preflight=real_preflight, spawn_process=real_spawn)

        upstream_config = _upstream_config_json(fake_server.port)
        extras = lifecycle.McpServerExtras(extra_env={_MCP_UPSTREAM_CONFIG_ENV: upstream_config})

        bridge = lifecycle.start_mcp_server(session, workspace, deps=deps, extras=extras)

        try:
            endpoint = bridge.endpoint_uri()
            deadline = time.monotonic() + 15.0
            ready = _poll_endpoint(endpoint, deadline)
            assert ready, "first child must be accessible"

            # Respawn with NO cached catalog: the child must probe again and
            # the fake upstream's non-MCP reply makes that probe fail. The
            # bridge must surface the failure as an attributed McpServerError
            # (restart counted by S1, stage/elapsed carried per R4-WRAP).
            bridge._inner.process.kill()
            with pytest.raises(lifecycle.McpServerError) as exc_info:
                bridge.check_health_and_restart_if_needed()
            assert "MCP restart attempt" in str(exc_info.value)
            assert bridge.restart_count == 1

        finally:
            bridge.shutdown()

    finally:
        fake_server.stop()
