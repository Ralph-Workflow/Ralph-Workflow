from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.agents.completion_signals import CompletionSignals
    from ralph.agents.execution_state import GenericExecutionStrategy, OpenCodeExecutionStrategy
    from ralph.agents.idle_watchdog import TimeoutPolicy, WaitingStatusListener
    from ralph.agents.invoke._workspace import WorkspaceMonitor
    from ralph.agents.timeout_clock import Clock
    from ralph.config.models import AgentConfig
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.process.liveness import LivenessProbe

if TYPE_CHECKING:

    class _EvalCompletionFn(Protocol):
        def __call__(
            self,
            workspace: Path,
            raw_output: list[str] | None = None,
            *,
            required_artifact: RequiredArtifact | None = None,
        ) -> CompletionSignals: ...


@dataclass(frozen=True)
class _AgentRunCtx:
    config: AgentConfig
    show_progress: bool
    extra_env: dict[str, str] | None
    workspace_path: Path | None
    policy: TimeoutPolicy
    execution_strategy: GenericExecutionStrategy | OpenCodeExecutionStrategy | None = None
    liveness_probe: LivenessProbe | None = None
    waiting_listener: WaitingStatusListener | None = None
    monitor: WorkspaceMonitor | None = None
    required_artifact: RequiredArtifact | None = None
    clock: Clock | None = None
    evaluate_completion_fn: _EvalCompletionFn | None = None
    expected_session_id: str | None = None
