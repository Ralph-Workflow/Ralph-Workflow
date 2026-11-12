from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.agents.clock import Clock
    from ralph.pipeline.effects import InvokeAgentEffect


@dataclass(frozen=True)
class AgentRecoveryInput:
    exc: Exception
    attempt_index: int
    max_recovery_attempts: int
    effect: InvokeAgentEffect
    workspace_root: Path
    raw_output: list[str]
    rendered_output: list[str]
    extracted_session_id: str | None
    inactivity_error_type: type[Exception]
    stale_session_id: str | None = None
    transport: str | None = None
    model: str | None = None
    run_id: str | None = None
    completion_reprompt_used: bool = False
    worker_namespace: Path | None = None
    clock: Clock = field(kw_only=True)
