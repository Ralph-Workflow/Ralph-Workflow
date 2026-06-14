"""Injectable dependencies for executing an agent-invocation effect."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.pipeline.factory import PipelineDeps
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
        ) -> Iterable[object]: ...

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


@dataclass(frozen=True)
class AgentExecutionDeps:
    """Invoke-specific dependencies bundled with the shared PipelineDeps.

    Lifecycle collaborators (bridge factory, prompt materializers, MCP
    supervisor, etc.) live in ``pipeline_deps``. This dataclass carries only
    the callbacks and registries that vary per invocation context.
    """

    pipeline_deps: PipelineDeps
    invoke_agent: _InvokeAgentFn
    agent_invocation_error: type[Exception]
    agent_registry: _AgentRegistryFactory
    show_phase_start_cb: _ShowPhaseStartFn | None = None
    set_session_id_cb: Callable[[str | None], None] | None = None


def build_agent_execution_deps(
    pipeline_deps: PipelineDeps,
    *,
    invoke_agent: _InvokeAgentFn,
    agent_invocation_error: type[Exception],
    agent_registry: _AgentRegistryFactory,
    show_phase_start_cb: _ShowPhaseStartFn | None = None,
    set_session_id_cb: Callable[[str | None], None] | None = None,
) -> AgentExecutionDeps:
    """Build an ``AgentExecutionDeps`` from a ``PipelineDeps`` and invoke callbacks."""
    return AgentExecutionDeps(
        pipeline_deps=pipeline_deps,
        invoke_agent=invoke_agent,
        agent_invocation_error=agent_invocation_error,
        agent_registry=agent_registry,
        show_phase_start_cb=show_phase_start_cb,
        set_session_id_cb=set_session_id_cb,
    )
