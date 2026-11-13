from __future__ import annotations

from dataclasses import KW_ONLY, dataclass, field
from typing import TYPE_CHECKING

from ralph.agents.idle_watchdog import TimeoutPolicy

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.agents.execution_state import BaseExecutionStrategy
    from ralph.agents.invoke._agent_run_ctx import _EvalCompletionFn
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.process.liveness import LivenessProbe


@dataclass(frozen=True)
class CompletionCheckOptions:
    execution_strategy: BaseExecutionStrategy | None = None
    workspace_path: Path | None = None
    liveness_probe: LivenessProbe | None = None
    policy: TimeoutPolicy = field(default_factory=lambda: TimeoutPolicy(idle_timeout_seconds=None))
    required_artifact: RequiredArtifact | None = None
    requires_completion_evidence: bool = True
    explicit_completion_seen: bool = False
    captured_session_id: str | None = None
    completion_run_id: str | None = None
    evaluate_completion_fn: _EvalCompletionFn | None = None
    _: KW_ONLY
    last_observed_tool_call: str | None = None
    last_evidence_summary: str | None = None
    elapsed_seconds: float | None = None
    has_meaningful_output: bool | None = None
    input_prompt: str | None = None
    transcript_tail: tuple[str, ...] = ()
    _sentinel_check_fn: Callable[[Path, str | None], bool] | None = field(default=None)
    sentinel_secret: str | None = None
    receipt_secret: str | None = None
    activity_only_supervision: bool = False
    agy_cli_log_path: Path | None = None


def _completion_run_id(opts: CompletionCheckOptions) -> str | None:
    return opts.completion_run_id or opts.captured_session_id


__all__ = ["CompletionCheckOptions", "_completion_run_id"]
