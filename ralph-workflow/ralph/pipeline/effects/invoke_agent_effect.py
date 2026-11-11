
from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.config.enums import PipelinePhase
else:
    PipelinePhase = import_module("ralph.config.enums").PipelinePhase


@dataclass(frozen=True)
class InvokeAgentEffect:

    agent_name: str
    phase: PipelinePhase
    prompt_file: str
    drain: str | None = None
    chain_name: str = ""
    run_id: str = ""
    iteration: str = ""
    requires_completion_evidence: bool = True
    activity_only_supervision: bool = False
    activity_only_operator_cap_seconds: float | None = None
    activity_only_status_interval_seconds: float | None = None
    activity_status_listener: Callable[[object], None] | None = None
    supervision_health_error: Callable[[], str | None] | None = None
