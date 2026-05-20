from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from ralph.pipeline.events import PipelineEvent

if TYPE_CHECKING:
    from ralph.workspace.memory import MemoryWorkspace


class MockAgentInvoker:
    """Mock that returns predefined success responses for agent invocations.

    This mock simulates:
    - AGENT_SUCCESS for planning and development phases
    - ANALYSIS_SUCCESS for analysis phases
    - COMMIT_SUCCESS for commit phases
    """

    AGENT_SUCCESS = PipelineEvent.AGENT_SUCCESS
    ANALYSIS_SUCCESS = PipelineEvent.ANALYSIS_SUCCESS
    COMMIT_SUCCESS = PipelineEvent.COMMIT_SUCCESS

    def __init__(self, workspace: MemoryWorkspace) -> None:
        self.workspace = workspace
        self.call_history: list[dict[str, Any]] = []
        self.call_counts: dict[str, int] = {}

    def invoke(self, agent_name: str, phase: str) -> PipelineEvent:
        self.call_counts[phase] = self.call_counts.get(phase, 0) + 1
        self.call_history.append({"agent": agent_name, "phase": phase})

        if phase in ("planning", "development", "review"):
            return cast("PipelineEvent", PipelineEvent.AGENT_SUCCESS)

        if "analysis" in phase:
            return cast("PipelineEvent", PipelineEvent.ANALYSIS_SUCCESS)

        if phase.endswith("_commit") or phase == "commit":
            return cast("PipelineEvent", PipelineEvent.COMMIT_SUCCESS)

        return cast("PipelineEvent", PipelineEvent.AGENT_SUCCESS)

    def count_for(self, phase: str) -> int:
        return self.call_counts.get(phase, 0)

    def commit_event_for(self, phase: str) -> PipelineEvent:
        """Return the event for commit phase completion.

        Only actual commit phases (with role='commit') should return
        COMMIT_SUCCESS. Phases like 'development_analysis_decision' that
        happen to contain 'commit' in the name should return AGENT_SUCCESS
        so they route through the normal analysis/execution flow.
        """
        # Actual commit phases end with '_commit' or are 'commit' itself
        if phase.endswith("_commit") or phase == "commit":
            return PipelineEvent.COMMIT_SUCCESS
        return PipelineEvent.AGENT_SUCCESS
