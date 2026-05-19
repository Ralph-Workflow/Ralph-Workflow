from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.events import AnalysisDecisionEvent, PipelineEvent
from tests.integration._mock_agent_invoker import MockAgentInvoker

if TYPE_CHECKING:
    from ralph.workspace.memory import MemoryWorkspace


class PlanningAnalysisRequestChangesOnceInvoker(MockAgentInvoker):
    """Request planning changes once, then approve if planning_analysis is re-entered."""

    def __init__(self, workspace: MemoryWorkspace) -> None:
        super().__init__(workspace)
        self.last_phase: str | None = None
        self._planning_analysis_calls = 0

    def invoke(self, agent_name: str, phase: str) -> PipelineEvent:
        self.last_phase = phase
        return super().invoke(agent_name, phase)

    def analysis_event_for(self, phase: str) -> AnalysisDecisionEvent | PipelineEvent:
        if phase == "planning_analysis":
            self._planning_analysis_calls += 1
            if self._planning_analysis_calls == 1:
                return AnalysisDecisionEvent(phase="planning_analysis", decision="request_changes")
            return AnalysisDecisionEvent(phase="planning_analysis", decision="completed")
        return PipelineEvent.ANALYSIS_SUCCESS
