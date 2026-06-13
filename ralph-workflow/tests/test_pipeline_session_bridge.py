"""Tests for the shared session-bridge module.

These tests are black-box and use injected fakes only: no real subprocess,
no real network, no time.sleep, no real file I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    MultimodalModelIdentity,
    resolve_capability_profile,
)
from ralph.mcp.protocol.env import MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV
from ralph.mcp.session_plan import SessionMcpPlan, SessionModelOpts
from ralph.pipeline.session_bridge import (
    BridgeFactory,
    BuildSessionMcpPlanFn,
    SessionBridgeLike,
    StartMcpServerFn,
    WorkspaceFactoryFn,
    bridge_env_for,
    build_session_bridge,
    reset_tool_registry_callback,
)
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.enums import AgentTransport
    from ralph.mcp.protocol.session import AgentSession
    from ralph.mcp.server.lifecycle import McpServerExtras
    from ralph.policy.models import AgentsPolicy
    from ralph.workspace.protocol import Workspace


class FakeSessionBridge:
    """Fake bridge implementing SessionBridgeLike."""

    def __init__(self, endpoint: str = "http://localhost:9999") -> None:
        self._endpoint = endpoint
        self.started = False
        self.shutdown_called = False
        self.reset_tool_registry_called = False

    def start(self) -> None:
        self.started = True

    def agent_endpoint_uri(self) -> str:
        return self._endpoint

    def endpoint_uri(self) -> str:
        return self._endpoint

    def shutdown(self) -> None:
        self.shutdown_called = True

    def reset_tool_registry(self) -> str:
        self.reset_tool_registry_called = True
        return "reset"


def fake_build_session_mcp_plan(
    transport: AgentTransport | None,
    drain: str,
    workspace_path: Path | None,
    agents_policy: AgentsPolicy | None,
    model_opts: SessionModelOpts | None,
    model_flag: str | None,
) -> SessionMcpPlan:
    identity = (
        model_opts.model_identity
        if model_opts is not None and model_opts.model_identity is not None
        else UNKNOWN_IDENTITY
    )
    return SessionMcpPlan(
        capabilities=frozenset({"workspace.read"}),
        server_env={"FAKE_ENV": "1"},
        model_identity=identity,
        capability_profile=resolve_capability_profile(identity),
    )


def fake_start_mcp_server(
    session: AgentSession,
    workspace: Workspace,
    extras: McpServerExtras | None = None,
) -> SessionBridgeLike:
    return FakeSessionBridge(endpoint="http://localhost:8888")


def fake_workspace_factory(root: Path) -> Workspace:
    return MemoryWorkspace(root)


class TestBuildSessionBridge:
    """Black-box tests for build_session_bridge."""

    def test_returns_bridge_with_endpoint(self, tmp_path: Path) -> None:
        bridge = build_session_bridge(
            workspace_root=tmp_path,
            drain="commit",
            agents_policy=None,
            build_session_mcp_plan_fn=fake_build_session_mcp_plan,
            start_mcp_server_fn=fake_start_mcp_server,
            workspace_factory=fake_workspace_factory,
        )

        assert isinstance(bridge, FakeSessionBridge)
        assert bridge.agent_endpoint_uri() == "http://localhost:8888"
        assert bridge.endpoint_uri() == "http://localhost:8888"
        assert bridge.started is True

    def test_threads_model_identity_into_plan(self, tmp_path: Path) -> None:
        model_identity = MultimodalModelIdentity(provider="claude", model_id="sonnet")
        bridge = build_session_bridge(
            workspace_root=tmp_path,
            drain="commit",
            agents_policy=None,
            model_identity=model_identity,
            build_session_mcp_plan_fn=fake_build_session_mcp_plan,
            start_mcp_server_fn=fake_start_mcp_server,
            workspace_factory=fake_workspace_factory,
        )

        assert isinstance(bridge, FakeSessionBridge)
        assert bridge.agent_endpoint_uri() == "http://localhost:8888"

    def test_defaults_to_unknown_identity_when_model_identity_is_none(
        self, tmp_path: Path
    ) -> None:
        bridge = build_session_bridge(
            workspace_root=tmp_path,
            drain="commit",
            agents_policy=None,
            build_session_mcp_plan_fn=fake_build_session_mcp_plan,
            start_mcp_server_fn=fake_start_mcp_server,
            workspace_factory=fake_workspace_factory,
        )

        assert isinstance(bridge, FakeSessionBridge)
        assert bridge.agent_endpoint_uri() == "http://localhost:8888"

    def test_passes_run_id_and_session_id_prefix(self, tmp_path: Path) -> None:
        bridge = build_session_bridge(
            workspace_root=tmp_path,
            drain="commit",
            agents_policy=None,
            session_id_prefix="commit",
            run_id="run-123",
            build_session_mcp_plan_fn=fake_build_session_mcp_plan,
            start_mcp_server_fn=fake_start_mcp_server,
            workspace_factory=fake_workspace_factory,
        )

        assert isinstance(bridge, FakeSessionBridge)
        assert bridge.agent_endpoint_uri() == "http://localhost:8888"

    def test_parallel_worker_flag_is_passed(self, tmp_path: Path) -> None:
        bridge = build_session_bridge(
            workspace_root=tmp_path,
            drain="commit",
            agents_policy=None,
            parallel_worker=True,
            build_session_mcp_plan_fn=fake_build_session_mcp_plan,
            start_mcp_server_fn=fake_start_mcp_server,
            workspace_factory=fake_workspace_factory,
        )

        assert isinstance(bridge, FakeSessionBridge)


class TestBridgeEnvFor:
    """Black-box tests for bridge_env_for."""

    def test_returns_exactly_two_keys(self, tmp_path: Path) -> None:
        bridge = FakeSessionBridge(endpoint="http://localhost:7777")
        env = bridge_env_for(bridge, run_id_label="commit-plumbing")

        assert set(env.keys()) == {str(MCP_ENDPOINT_ENV), str(MCP_RUN_ID_ENV)}
        assert env[str(MCP_ENDPOINT_ENV)] == "http://localhost:7777"
        assert env[str(MCP_RUN_ID_ENV)] == "commit-plumbing"


class TestResetToolRegistryCallback:
    """Black-box tests for reset_tool_registry_callback."""

    def test_returns_none_when_bridge_is_none(self) -> None:
        assert reset_tool_registry_callback(None) is None

    def test_returns_callable_when_reset_tool_registry_exists(self, tmp_path: Path) -> None:
        bridge = FakeSessionBridge()
        callback = reset_tool_registry_callback(bridge)

        assert callback is not None
        assert callback() == "reset"
        assert bridge.reset_tool_registry_called is True

    def test_returns_none_when_reset_tool_registry_missing(self, tmp_path: Path) -> None:
        class NoResetBridge:
            pass

        assert reset_tool_registry_callback(NoResetBridge()) is None


class TestProtocolAliases:
    """Ensure callable aliases are importable and structural."""

    def test_bridge_factory_protocol_is_callable(self, tmp_path: Path) -> None:
        class FakeBridgeFactory:
            def __call__(
                self,
                *,
                workspace_root: Path,
                drain: str,
                agents_policy: AgentsPolicy | None,
                transport: AgentTransport | None = None,
                capabilities: frozenset[str] | None = None,
                session_id_prefix: str | None = None,
                run_id: str | None = None,
                model_identity: MultimodalModelIdentity | None = None,
                parallel_worker: bool = False,
                build_session_mcp_plan_fn: BuildSessionMcpPlanFn | None = None,
                start_mcp_server_fn: StartMcpServerFn | None = None,
                workspace_factory: WorkspaceFactoryFn | None = None,
            ) -> SessionBridgeLike:
                return FakeSessionBridge()

        factory: BridgeFactory = FakeBridgeFactory()
        bridge = factory(
            workspace_root=tmp_path,
            drain="commit",
            agents_policy=None,
        )
        assert isinstance(bridge, FakeSessionBridge)
