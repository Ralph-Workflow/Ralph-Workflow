from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.mcp.server.factory import McpServerFactory
from ralph.mcp.server.factory_impl import DynamicBindingMcpServerFactory
from ralph.mcp.server.lifecycle import McpRestartPolicy, RestartAwareMcpBridge
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.server import lifecycle

from tests._mcp_factory_impl_helper_fakebridge import FakeBridge
from tests._mcp_factory_impl_helper_fakeprocess import FakeProcess


def test_factory_is_runtime_checkable_protocol(tmp_path: Path) -> None:
    factory = DynamicBindingMcpServerFactory(workspace=FsWorkspace(tmp_path))

    assert isinstance(factory, McpServerFactory)


def test_build_creates_handles_with_distinct_endpoints(tmp_path: Path) -> None:
    seen: list[tuple[object, object, object]] = []

    def fake_start_server(
        session: object,
        workspace: object,
        *,
        deps: lifecycle.LifecycleDeps | None = None,
    ) -> FakeBridge:
        assert deps is not None
        _host, port, sock = deps.reserve_listening_socket()
        sock.close()
        endpoint = f"http://127.0.0.1:{port}/mcp"
        seen.append((session, workspace, endpoint))
        return FakeBridge(endpoint=endpoint, pid=port)

    factory = DynamicBindingMcpServerFactory(
        workspace=FsWorkspace(tmp_path),
        start_server=fake_start_server,
    )
    session = AgentSession(session_id="session-1", run_id="run-1", drain="planning")

    first = factory.build(session)
    second = factory.build(session)

    assert first.endpoint != second.endpoint
    assert first.pid != second.pid
    assert seen == [
        (session, factory.workspace, first.endpoint),
        (session, factory.workspace, second.endpoint),
    ]


def test_handle_shutdown_calls_bridge_shutdown(tmp_path: Path) -> None:
    bridge = FakeBridge(endpoint="http://127.0.0.1:43123/mcp", pid=43123)

    def fake_start_server(
        session: object,
        workspace: object,
        *,
        deps: lifecycle.LifecycleDeps | None = None,
    ) -> FakeBridge:
        del session, workspace, deps
        return bridge

    factory = DynamicBindingMcpServerFactory(
        workspace=FsWorkspace(tmp_path),
        start_server=fake_start_server,
    )
    session = AgentSession(session_id="session-1", run_id="run-1", drain="planning")

    handle = factory.build(session)
    handle.shutdown()

    assert bridge.shutdown_calls == 1


def test_build_accepts_restart_aware_bridge_from_real_lifecycle(tmp_path: Path) -> None:
    inner = StandaloneMcpProcess(
        endpoint="http://127.0.0.1:43999/mcp",
        process=FakeProcess(43999),
        session_file=tmp_path / "session.json",
    )
    bridge = RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: inner,
        restart_policy=McpRestartPolicy(),
        run_id="test-run",
    )

    def fake_start_server(
        session: object,
        workspace: object,
        *,
        deps: lifecycle.LifecycleDeps | None = None,
    ) -> RestartAwareMcpBridge:
        del session, workspace, deps
        return bridge

    factory = DynamicBindingMcpServerFactory(
        workspace=FsWorkspace(tmp_path),
        start_server=fake_start_server,
    )
    session = AgentSession(session_id="session-1", run_id="run-1", drain="development")

    handle = factory.build(session)

    assert handle.pid == 43999
    assert handle.endpoint == "http://127.0.0.1:43999/mcp"


def test_handle_shutdown_releases_allocated_endpoint(tmp_path: Path) -> None:
    bridges: list[FakeBridge] = []

    def fake_start_server(
        session: object,
        workspace: object,
        *,
        deps: lifecycle.LifecycleDeps | None = None,
    ) -> FakeBridge:
        assert deps is not None
        _host, port, sock = deps.reserve_listening_socket()
        sock.close()
        bridge = FakeBridge(endpoint=f"http://127.0.0.1:{port}/mcp", pid=port)
        bridges.append(bridge)
        return bridge

    factory = DynamicBindingMcpServerFactory(
        workspace=FsWorkspace(tmp_path),
        start_server=fake_start_server,
    )
    session = AgentSession(session_id="session-1", run_id="run-1", drain="planning")

    first = factory.build(session)
    second = factory.build(session)
    assert len(factory._allocated_endpoints) == 2, "two live handles must reserve two endpoints"

    first.shutdown()
    assert first.endpoint not in factory._allocated_endpoints, (
        "shutdown must release the first endpoint from _allocated_endpoints"
    )
    assert second.endpoint in factory._allocated_endpoints, (
        "the second endpoint must still be reserved while its handle is live"
    )

    second.shutdown()
    assert not factory._allocated_endpoints, (
        "all endpoints must be released once every handle has been shut down"
    )
    for bridge in bridges:
        assert bridge.shutdown_calls == 1


def test_handle_shutdown_releases_endpoint_even_when_bridge_raises(tmp_path: Path) -> None:
    bridges: list[FakeBridge] = []

    def fake_start_server(
        session: object,
        workspace: object,
        *,
        deps: lifecycle.LifecycleDeps | None = None,
    ) -> FakeBridge:
        del session, workspace
        assert deps is not None
        _host, port, sock = deps.reserve_listening_socket()
        sock.close()
        bridge = FakeBridge(
            endpoint=f"http://127.0.0.1:{port}/mcp",
            pid=port,
            raise_on_shutdown=RuntimeError("bridge shutdown exploded"),
        )
        bridges.append(bridge)
        return bridge

    factory = DynamicBindingMcpServerFactory(
        workspace=FsWorkspace(tmp_path),
        start_server=fake_start_server,
    )
    session = AgentSession(session_id="session-1", run_id="run-1", drain="planning")

    handle = factory.build(session)
    assert handle.endpoint in factory._allocated_endpoints

    with pytest.raises(RuntimeError, match="bridge shutdown exploded"):
        handle.shutdown()

    assert handle.endpoint not in factory._allocated_endpoints, (
        "endpoint MUST be released even when the bridge shutdown raises"
    )
    assert bridges[0].shutdown_calls == 1


def test_build_releases_endpoint_when_start_server_raises(tmp_path: Path) -> None:
    raised = {"count": 0}
    reserved_sockets: list[object] = []

    def fake_start_server_raises(
        session: object,
        workspace: object,
        *,
        deps: lifecycle.LifecycleDeps | None = None,
    ) -> FakeBridge:
        del session, workspace
        assert deps is not None
        _host, _port, sock = deps.reserve_listening_socket()
        reserved_sockets.append(sock)
        raised["count"] += 1
        msg = "preflight failed: tool unreachable"
        raise RuntimeError(msg)

    factory = DynamicBindingMcpServerFactory(
        workspace=FsWorkspace(tmp_path),
        start_server=fake_start_server_raises,
    )
    session = AgentSession(session_id="session-1", run_id="run-1", drain="planning")

    with pytest.raises(RuntimeError, match="preflight failed"):
        factory.build(session)

    assert not factory._allocated_endpoints, (
        "startup failure MUST release the reserved endpoint so the next build can reuse the port"
    )
    assert raised["count"] == 1
    assert all(sock.fileno() == -1 for sock in reserved_sockets), (
        "startup failure MUST close the reserved listening socket"
    )

    def fake_start_server_ok(
        session: object,
        workspace: object,
        *,
        deps: lifecycle.LifecycleDeps | None = None,
    ) -> FakeBridge:
        del session, workspace
        assert deps is not None
        _host, port, sock = deps.reserve_listening_socket()
        sock.close()
        return FakeBridge(endpoint=f"http://127.0.0.1:{port}/mcp", pid=port)

    ok_factory = DynamicBindingMcpServerFactory(
        workspace=FsWorkspace(tmp_path),
        start_server=fake_start_server_ok,
    )
    handle = ok_factory.build(session)
    port = int(handle.endpoint.rsplit(":", 1)[1].split("/")[0])
    assert handle.pid == port


def test_build_releases_endpoint_when_post_startup_extraction_fails(
    tmp_path: Path,
) -> None:

    class _FailingEndpointBridge(FakeBridge):
        def agent_endpoint_uri(self) -> str:
            msg = "agent endpoint vanished after startup"
            raise RuntimeError(msg)

    def fake_start_server(
        session: object,
        workspace: object,
        *,
        deps: lifecycle.LifecycleDeps | None = None,
    ) -> _FailingEndpointBridge:
        del session, workspace
        assert deps is not None
        _host, port, sock = deps.reserve_listening_socket()
        sock.close()
        return _FailingEndpointBridge(endpoint=f"http://127.0.0.1:{port}/mcp", pid=port)

    factory = DynamicBindingMcpServerFactory(
        workspace=FsWorkspace(tmp_path),
        start_server=fake_start_server,
    )
    session = AgentSession(session_id="session-1", run_id="run-1", drain="planning")

    with pytest.raises(RuntimeError, match="agent endpoint vanished after startup"):
        factory.build(session)

    assert not factory._allocated_endpoints, (
        "post-startup extraction failure MUST release the reserved endpoint "
        "so the next build can reuse the port"
    )


def test_build_releases_endpoint_when_bridge_pid_extraction_fails(
    tmp_path: Path,
) -> None:

    class _BridgeWithoutProcess:
        def __init__(self, endpoint: str) -> None:
            self._endpoint = endpoint

        def agent_endpoint_uri(self) -> str:
            return self._endpoint

        def shutdown(self) -> None:
            pass

    def fake_start_server(
        session: object,
        workspace: object,
        *,
        deps: lifecycle.LifecycleDeps | None = None,
    ) -> _BridgeWithoutProcess:
        del session, workspace
        assert deps is not None
        _host, port, sock = deps.reserve_listening_socket()
        sock.close()
        return _BridgeWithoutProcess(endpoint=f"http://127.0.0.1:{port}/mcp")

    factory = DynamicBindingMcpServerFactory(
        workspace=FsWorkspace(tmp_path),
        start_server=fake_start_server,
    )
    session = AgentSession(session_id="session-1", run_id="run-1", drain="planning")

    with pytest.raises(TypeError, match=r"MCP server bridge must expose process\.pid"):
        factory.build(session)

    assert not factory._allocated_endpoints, (
        "_bridge_pid failure MUST release the reserved endpoint so the "
        "next build can reuse the port"
    )


def _fake_process_with_poll(pid: int) -> object:
    from tests._mcp_factory_impl_helper_fakeprocess import FakeProcess

    class _P(FakeProcess):
        def poll(self) -> int:
            return 0

        def terminate(self, grace_period_s: float = 5.0) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            return 0

    return _P(pid)


def test_factory_uses_listening_socket_not_port_number(tmp_path: Path) -> None:
    reserved: dict[str, object] = {}

    def fake_start_server(
        session: object,
        workspace: object,
        *,
        deps: lifecycle.LifecycleDeps | None = None,
    ) -> FakeBridge:
        del session, workspace
        assert deps is not None
        host, port, sock = deps.reserve_listening_socket()
        reserved["host"] = host
        reserved["port"] = port
        reserved["sock"] = sock
        return FakeBridge(endpoint=f"http://127.0.0.1:{port}/mcp", pid=43123)

    factory = DynamicBindingMcpServerFactory(
        workspace=FsWorkspace(tmp_path),
        start_server=fake_start_server,
    )
    session = AgentSession(session_id="session-1", run_id="run-1", drain="planning")

    handle = factory.build(session)

    port = reserved["port"]
    assert isinstance(port, int)
    assert handle.endpoint == f"http://127.0.0.1:{port}/mcp", (
        "the handle endpoint must come from the reserved listening socket"
    )
    assert f"http://127.0.0.1:{port}/mcp" in factory._allocated_endpoints
    assert reserved["sock"].fileno() != -1
    handle.shutdown()


def test_factory_releases_listening_socket_on_shutdown(tmp_path: Path) -> None:
    captured_sock: list[object] = []

    def fake_start_server(
        session: object,
        workspace: object,
        *,
        deps: lifecycle.LifecycleDeps | None = None,
    ) -> RestartAwareMcpBridge:
        del session, workspace
        assert deps is not None
        _host, port, sock = deps.reserve_listening_socket()
        captured_sock.append(sock)
        inner = StandaloneMcpProcess(
            endpoint=f"http://127.0.0.1:{port}/mcp",
            process=_fake_process_with_poll(43999),
            session_file=tmp_path / "session.json",
        )
        return RestartAwareMcpBridge(
            inner,
            restart_fn=lambda: inner,
            restart_policy=McpRestartPolicy(),
            run_id="test-run",
        )

    factory = DynamicBindingMcpServerFactory(
        workspace=FsWorkspace(tmp_path),
        start_server=fake_start_server,
    )
    session = AgentSession(session_id="session-1", run_id="run-1", drain="planning")

    handle = factory.build(session)
    assert handle.endpoint in factory._allocated_endpoints

    handle.shutdown()

    assert handle.endpoint not in factory._allocated_endpoints
    assert len(captured_sock) == 1
    sock = captured_sock[0]
    assert sock.fileno() == -1
