
from __future__ import annotations

from enum import StrEnum

from ralph.project_policy.pipeline_graph import PHASE_ANALYSIS, PHASE_REMEDIATION


class PolicyMode(StrEnum):

    NORMAL = "normal"
    REDO = "redo"
    REDO_ONLY = "redo_only"
    RUN_AGENTS = "run_agents"
    RUN_AGENTS_ONLY = "run_agents_only"

    def is_explicit(self) -> bool:
        return self is not PolicyMode.NORMAL

    def resets_policy(self) -> bool:
        return self in (PolicyMode.REDO, PolicyMode.REDO_ONLY)

    def exits_after(self) -> bool:
        return self in (PolicyMode.REDO_ONLY, PolicyMode.RUN_AGENTS_ONLY)

    def entry_phase(self) -> str:
        if self in (PolicyMode.RUN_AGENTS, PolicyMode.RUN_AGENTS_ONLY):
            return PHASE_ANALYSIS
        return PHASE_REMEDIATION


__all__ = ["PolicyMode"]
