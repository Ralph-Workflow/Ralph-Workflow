"""Canonical invoker that forces every planning analysis run to request changes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.events import AnalysisDecisionEvent, PipelineEvent
from tests.integration._mock_agent_invoker import MockAgentInvoker

if TYPE_CHECKING:
    from ralph.workspace.memory import MemoryWorkspace


class PlanningAnalysisAlwaysLoopbackInvoker(MockAgentInvoker):
    """Force every planning analysis run to request changes."""

    def __init__(self, workspace: MemoryWorkspace) -> None:
        super().__init__(workspace)
        self.last_phase: str | None = None

    def invoke(self, agent_name: str, phase: str) -> PipelineEvent:
        self.last_phase = phase
        return super().invoke(agent_name, phase)

    def analysis_event_for(self, phase: str) -> AnalysisDecisionEvent | PipelineEvent:
        if phase == "planning_analysis":
            return AnalysisDecisionEvent(phase="planning_analysis", decision="request_changes")
        return PipelineEvent.ANALYSIS_SUCCESS
