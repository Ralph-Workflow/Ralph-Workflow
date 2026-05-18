"""Internal context for agent invocation execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.config.enums import Verbosity
from ralph.pipeline.agent_execution_deps import AgentExecutionDeps
from ralph.pipeline.effects import InvokeAgentEffect

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.display.subscriber import PipelineSubscriber
    from ralph.pipeline.legacy_console_display import LegacyConsoleDisplay
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import AgentsPolicy, PolicyBundle
    from ralph.workspace.scope import WorkspaceScope


@dataclass(frozen=True)
class _AgentInvocationCtx:
    effect: InvokeAgentEffect
    config: UnifiedConfig
    deps: AgentExecutionDeps
    workspace_scope: WorkspaceScope
    verbosity: Verbosity
    resolved_display_context: DisplayContext | None
    display_subscriber: PipelineSubscriber | None
    max_recovery_attempts: int
    effective_agents_policy: AgentsPolicy
    state: PipelineState | None
    policy_bundle: PolicyBundle | None
    waiting_listener: Callable[[object], None]
    agent_config: AgentConfig
    display: ParallelDisplay | LegacyConsoleDisplay | None
