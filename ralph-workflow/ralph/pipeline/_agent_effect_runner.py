from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections import deque
    from collections.abc import Callable

    from ralph.agents.invoke import AttemptEnded
    from ralph.config.enums import Verbosity
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.mcp.server.lifecycle import RestartAwareMcpBridge
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.pipeline.attempt_finalizer import AttemptFinalizer
    from ralph.pipeline.effects import InvokeAgentEffect
    from ralph.pipeline.events import AgentFailureEvent, PipelineEvent
    from ralph.pipeline.factory import PipelineDeps
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PolicyBundle
    from ralph.workspace.scope import WorkspaceScope


class AgentEffectRunner(Protocol):
    def __call__(
        self,
        effect: InvokeAgentEffect,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: WorkspaceScope,
        *,
        display_context: DisplayContext | None,
        display: ParallelDisplay | None,
        verbosity: Verbosity,
        state: PipelineState | None,
        policy_bundle: PolicyBundle | None,
        bridge: RestartAwareMcpBridge | None,
        raw_output_sink: deque[str] | None,
        rendered_output_sink: deque[str] | None,
        raw_line_sink: Callable[[str], None] | None,
        run_id: str | None,
        required_artifact: RequiredArtifact | None,
        session_id: str | None,
        extra_env: dict[str, str] | None,
        raise_resumable_exit: bool,
        agent_invocation_error_sink: Callable[[Exception], object] | None,
        attempt_finalizer: AttemptFinalizer | None,
        **opts: object,
    ) -> PipelineEvent | AgentFailureEvent | AttemptEnded: ...
