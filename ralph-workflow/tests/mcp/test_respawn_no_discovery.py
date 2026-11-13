from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
import urllib.parse
import urllib.request
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.upstream.models import UpstreamTool
from ralph.mcp.upstream.tool_catalog_cache import cache_tool_catalog, reset_tool_catalog_cache
from ralph.process.manager import ProcessTerminationIssuer
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from ralph.mcp.server._lifecycle_deps import LifecycleDeps

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.required_auto_integrate_e2e]


_FAKE_TOOL = UpstreamTool(
    name="fake-upstream-tool",
    description="Tool registered via the pre-populated tool catalog.",
    input_schema={"type": "object"},
)


class _FakeMcpUpstreamServer:
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
                client, _ = sock.accept()
                try:
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
        try:
            client.settimeout(30.0)
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
            header_end = header_bytes.index(b"\r\n\r\n") + 4
            body = header_bytes[header_end:]
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
            pass

    def stop(self) -> None:
        self._stop_event.set()
        if self._server_socket is not None and self._port is not None:
            with (
                socket.socket(socket.AF_INET, socket.SOCK_STREAM) as wakeup,
                contextlib.suppress(OSError),
            ):
                wakeup.connect(("127.0.0.1", self._port))
        if self._thread is not None:
            self._thread.join(timeout=5.0)


_MCP_UPSTREAM_CONFIG_ENV = "RALPH_UPSTREAM_MCP_CONFIG"


def _make_session() -> AgentSession:
    return AgentSession(
        session_id="test-respawn-no-discovery",
        run_id="run-respawn-no-discovery",
        drain="development",
        capabilities={"WorkspaceRead", "UpstreamToolUse"},
    )


def _upstream_config_json(port: int) -> str:
    config: list[dict[str, str]] = [
        {
            "name": "fake-upstream",
            "transport": "http",
            "url": f"http://127.0.0.1:{port}/mcp",
        }
    ]
    return json.dumps(config, separators=(",", ":"))


def _fetch_endpoint_tool_names(endpoint: str, *, timeout: float = 5.0) -> list[str]:
    request_payload: dict[str, object] = {
        "jsonrpc": "2.0",
        "method": "tools/list",
        "id": "1",
        "params": {},
    }
    body = json.dumps(request_payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read(1024 * 1024).decode("utf-8", errors="replace")
    if not raw:
        return []
    data_lines = [line[len("data: ") :] for line in raw.splitlines() if line.startswith("data: ")]
    payload_str = data_lines[0] if data_lines else raw
    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    result = payload.get("result")
    if not isinstance(result, dict):
        return []
    tools = result.get("tools")
    if not isinstance(tools, list):
        return []
    return [
        entry["name"]
        for entry in tools
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    ]


_CHILD_SERVING_TIMEOUT_S = 15.0


def _wait_child_serving_tool_names(endpoint: str) -> list[str]:
    return _fetch_endpoint_tool_names(endpoint, timeout=_CHILD_SERVING_TIMEOUT_S)


def _poll_endpoint(endpoint: str, deadline: float) -> bool:
    port = int(urllib.parse.urlsplit(endpoint).port or 0)
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.02)
    return False


def _make_deps(
    tmp_path: Path,
    real_spawn_process: lifecycle.SpawnProcess,
) -> LifecycleDeps:

    def noop_preflight(endpoint: str, required_tools: list[str], timeout: timedelta) -> None:
        del endpoint, required_tools, timeout

    default_deps = lifecycle._default_lifecycle_deps()
    return default_deps.__replace__(preflight=noop_preflight, spawn_process=real_spawn_process)


@pytest.mark.timeout_seconds(45)
def test_respawn_with_cached_catalog_makes_zero_discovery_calls(tmp_path: Path) -> None:
    fake_server = _FakeMcpUpstreamServer()
    fake_server.start()
    try:
        cache_tool_catalog(
            tmp_path,
            {"fake-upstream": [_FAKE_TOOL]},
        )

        upstream_config = _upstream_config_json(fake_server.port)

        session = _make_session()
        workspace = FsWorkspace(tmp_path)
        real_spawn = lifecycle._default_lifecycle_deps().spawn_process
        deps = _make_deps(tmp_path, real_spawn)

        extras = lifecycle.McpServerExtras(
            extra_env={_MCP_UPSTREAM_CONFIG_ENV: upstream_config},
            restart_policy=lifecycle.McpRestartPolicy(cooldown_seconds=0.0),
        )

        bridge = lifecycle.start_mcp_server(
            session,
            workspace,
            deps=deps,
            extras=extras,
        )

        try:
            endpoint = bridge.endpoint_uri()
            deadline = time.monotonic() + 10.0
            ready = _poll_endpoint(endpoint, deadline)
            assert ready, f"first child endpoint {endpoint} must be accessible"
            _wait_child_serving_tool_names(endpoint)

            count_after_first = fake_server.tools_list_count

            stale_inner = bridge._inner
            stale_inner.process.kill(issuer=ProcessTerminationIssuer.OPERATOR)
            assert bridge.check_health_and_restart_if_needed() is True

            deadline = time.monotonic() + 10.0
            ready = _poll_endpoint(endpoint, deadline)
            assert ready, f"respawned child endpoint {endpoint} must be accessible"

            assert bridge._inner is not stale_inner, (
                "restart must replace the inner StandaloneMcpProcess"
            )
            respawned = _wait_child_serving_tool_names(endpoint)
            assert any("fake-upstream-tool" in name for name in respawned), respawned[:30]

            count_after_respawn = fake_server.tools_list_count
            delta = count_after_respawn - count_after_first

            assert delta == 0, (
                f"respawn child must NOT call fake upstream tools/list; "
                f"got {delta} new call(s) (count before={count_after_first}, "
                f"count after={count_after_respawn}); "
                "UPSTREAM_MCP_TOOL_CATALOG_ENV may not be set in the respawn path, "
                "or the production respawn path does not pass the catalog"
            )

            assert bridge.process.poll() is None, "respawned child must be alive"

        finally:
            bridge.shutdown()

    finally:
        fake_server.stop()


@pytest.mark.timeout_seconds(20)
def test_respawn_increments_restart_count(tmp_path: Path) -> None:
    fake_server = _FakeMcpUpstreamServer()
    fake_server.start()
    try:
        cache_tool_catalog(tmp_path, {"fake-upstream": [_FAKE_TOOL]})

        session = _make_session()
        workspace = FsWorkspace(tmp_path)
        real_spawn = lifecycle._default_lifecycle_deps().spawn_process
        deps = _make_deps(tmp_path, real_spawn)
        upstream_config = _upstream_config_json(fake_server.port)
        extras = lifecycle.McpServerExtras(
            extra_env={_MCP_UPSTREAM_CONFIG_ENV: upstream_config},
            restart_policy=lifecycle.McpRestartPolicy(cooldown_seconds=0.0),
        )

        bridge = lifecycle.start_mcp_server(session, workspace, deps=deps, extras=extras)
        try:
            assert bridge.restart_count == 0

            bridge._inner.process.kill(issuer=ProcessTerminationIssuer.OPERATOR)
            assert bridge.check_health_and_restart_if_needed() is True
            assert bridge.restart_count == 1, "restart_count must be 1 after one respawn"

            bridge._inner.process.kill(issuer=ProcessTerminationIssuer.OPERATOR)
            assert bridge.check_health_and_restart_if_needed() is True
            assert bridge.restart_count == 2, "restart_count must be 2 after two respawns"
        finally:
            bridge.shutdown()
    finally:
        fake_server.stop()


@pytest.mark.timeout_seconds(45)
def test_cold_first_start_then_warm_respawn_zero_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RALPH_MCP_UPSTREAM_PROBE_TIMEOUT_MS", "3000")
    monkeypatch.setenv("RALPH_MCP_PREFLIGHT_TIMEOUT_MS", "20000")
    fake_server = _FakeMcpUpstreamServer()
    fake_server.start()
    try:
        reset_tool_catalog_cache()
        session = _make_session()
        workspace = FsWorkspace(tmp_path)

        real_spawn = lifecycle._default_lifecycle_deps().spawn_process
        deps = _make_deps(tmp_path, real_spawn)

        upstream_config = _upstream_config_json(fake_server.port)
        extras = lifecycle.McpServerExtras(
            extra_env={_MCP_UPSTREAM_CONFIG_ENV: upstream_config},
            restart_policy=lifecycle.McpRestartPolicy(cooldown_seconds=0.0),
        )

        bridge = lifecycle.start_mcp_server(session, workspace, deps=deps, extras=extras)

        try:
            endpoint = bridge.endpoint_uri()
            deadline = time.monotonic() + 20.0
            ready = _poll_endpoint(endpoint, deadline)
            assert ready, f"first child endpoint {endpoint} must be accessible"

            catalog_path = tmp_path / ".agent" / "mcp" / "parent_catalog.json"
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                if catalog_path.exists() and fake_server.tools_list_count >= 1:
                    break
                time.sleep(0.02)
            assert catalog_path.exists(), (
                "first start must publish parent_catalog.json (runtime.py first-start call site)"
            )
            assert fake_server.tools_list_count == 1, (
                f"first start must probe the fake upstream exactly once; "
                f"got {fake_server.tools_list_count} tools/list call(s)"
            )

            bridge._inner.process.kill(issuer=ProcessTerminationIssuer.OPERATOR)
            assert bridge.check_health_and_restart_if_needed() is True

            deadline = time.monotonic() + 15.0
            ready = _poll_endpoint(endpoint, deadline)
            assert ready, f"respawned child endpoint {endpoint} must be accessible"
            served = _wait_child_serving_tool_names(endpoint)

            delta = fake_server.tools_list_count - 1
            assert delta == 0, (
                f"respawned child must NOT re-probe the fake upstream; "
                f"got {delta} new tools/list call(s) — the published "
                "parent_catalog.json snapshot may not have been applied "
                "to the respawn env"
            )

            assert bridge.process.poll() is None, "respawned child must be alive"
            assert any("fake-upstream-tool" in name for name in served), served[:30]

        finally:
            bridge.shutdown()

    finally:
        fake_server.stop()


@pytest.mark.timeout_seconds(20)
def test_absent_or_corrupt_snapshot_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_server = _FakeMcpUpstreamServer()
    fake_server.start()
    try:
        cache_tool_catalog(tmp_path, {"fake-upstream": [_FAKE_TOOL]})
        catalog_path = tmp_path / ".agent" / "mcp" / "parent_catalog.json"
        assert catalog_path.exists()

        session = _make_session()
        workspace = FsWorkspace(tmp_path)
        deps = lifecycle._default_lifecycle_deps()
        upstream_config = _upstream_config_json(fake_server.port)
        extras = lifecycle.McpServerExtras(
            extra_env={_MCP_UPSTREAM_CONFIG_ENV: upstream_config},
            restart_policy=lifecycle.McpRestartPolicy(cooldown_seconds=0.0),
        )

        monkeypatch.setenv("RALPH_MCP_PREFLIGHT_TIMEOUT_MS", "5000")
        bridge = lifecycle.start_mcp_server(session, workspace, deps=deps, extras=extras)
        try:
            monkeypatch.setenv("RALPH_MCP_PREFLIGHT_TIMEOUT_MS", "5000")

            catalog_path.write_text("{not valid json", encoding="utf-8")

            bridge._inner.process.kill(issuer=ProcessTerminationIssuer.OPERATOR)
            assert bridge.check_health_and_restart_if_needed() is False
            assert bridge.restart_count == 1
        finally:
            bridge.shutdown()

    finally:
        fake_server.stop()
