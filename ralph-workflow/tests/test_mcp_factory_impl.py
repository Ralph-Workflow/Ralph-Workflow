from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.mcp.server.factory import McpServerFactory
from ralph.mcp.server.factory_impl import DynamicBindingMcpServerFactory
from ralph.mcp.server.lifecycle import McpRestartPolicy, RestartAwareMcpBridge
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.server import lifecycle

from tests.test_mcp_factory_impl_helper_fakebridge import FakeBridge
from tests.test_mcp_factory_impl_helper_fakeprocess import FakeProcess


def test_factory_is_runtime_checkable_protocol(tmp_path: Path) -> None:
    factory = DynamicBindingMcpServerFactory(workspace=FsWorkspace(tmp_path))

    assert isinstance(factory, McpServerFactory)


def test_build_creates_handles_with_distinct_endpoints(tmp_path: Path) -> None:
    seen: list[tuple[object, object, object]] = []

    def fake_reserve_port() -> int:
        return 43000 + len(seen) + 1

    def fake_start_server(
        session: object,
        workspace: object,
        *,
        deps: lifecycle.LifecycleDeps | None = None,
    ) -> FakeBridge:
        assert deps is not None
        port = deps.reserve_port()
        endpoint = f"http://127.0.0.1:{port}/mcp"
        seen.append((session, workspace, endpoint))
        return FakeBridge(endpoint=endpoint, pid=port)

    factory = DynamicBindingMcpServerFactory(
        workspace=FsWorkspace(tmp_path),
        reserve_port=fake_reserve_port,
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
    """The production start_mcp_server returns a RestartAwareMcpBridge.

    The factory must extract the pid from it; the parallel worker session
    path hard-fails otherwise (every fan-out worker dies at session setup).
    """
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
