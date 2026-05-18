"""Injectable dependencies for executing an agent-invocation effect."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager
    from pathlib import Path

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.mcp.protocol.startup import HeartbeatPolicy
    from ralph.mcp.server.lifecycle import RestartAwareMcpBridge
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PipelinePolicy


if TYPE_CHECKING:

    class _InvokeAgentFn(Protocol):
        def __call__(
            self,
            config: AgentConfig,
            prompt_file: str,
            *,
            options: InvokeOptions | None = None,
        ) -> object: ...

    class _RegistryLike(Protocol):
        def get(self, name: str) -> AgentConfig | None: ...

    class _AgentRegistryFactory(Protocol):
        @classmethod
        def from_config(cls, config: UnifiedConfig) -> _RegistryLike: ...

    class _ShowPhaseStartFn(Protocol):
        def __call__(
            self,
            phase: str,
            agent_name: str,
            display_context: DisplayContext,
            state: PipelineState,
            *,
            pipeline_policy: PipelinePolicy,
        ) -> None: ...

    class _StartMcpServerFn(Protocol):
        def __call__(self, *args: object, **kwargs: object) -> RestartAwareMcpBridge: ...

    class _ShutdownMcpServerFn(Protocol):
        def __call__(self, bridge: object) -> None: ...

    class _CheckMcpBridgeHealthFn(Protocol):
        def __call__(self, bridge: object) -> None: ...

    class _MaterializeSystemPromptFn(Protocol):
        def __call__(self, *, workspace_root: Path, name: str) -> str: ...

    class _McpSupervisorFactory(Protocol):
        def __call__(
            self,
            bridge: object,
            *,
            check_interval: object,
            on_restart: object,
        ) -> AbstractContextManager[None]: ...

    class _HeartbeatPolicyFromEnvFn(Protocol):
        def __call__(self) -> HeartbeatPolicy: ...


@dataclass(frozen=True)
class AgentExecutionDeps:
    """Injectable dependencies for executing an agent-invocation effect."""

    invoke_agent: _InvokeAgentFn
    agent_invocation_error: type[Exception]
    agent_registry: _AgentRegistryFactory
    show_phase_start_cb: _ShowPhaseStartFn | None = None
    set_session_id_cb: Callable[[str | None], None] | None = None
    start_mcp_server_fn: _StartMcpServerFn | None = None
    shutdown_mcp_server_fn: _ShutdownMcpServerFn | None = None
    check_mcp_bridge_health_fn: _CheckMcpBridgeHealthFn | None = None
    materialize_system_prompt_fn: _MaterializeSystemPromptFn | None = None
    mcp_supervisor_factory: _McpSupervisorFactory | None = None
    heartbeat_policy_from_env_fn: _HeartbeatPolicyFromEnvFn | None = None
