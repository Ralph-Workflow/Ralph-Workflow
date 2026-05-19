"""Canonical invoker that forces every development analysis run to request changes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.events import PipelineEvent
from tests.integration._mock_agent_invoker import MockAgentInvoker

if TYPE_CHECKING:
    from ralph.workspace.memory import MemoryWorkspace


class DevelopmentAnalysisAlwaysLoopbackInvoker(MockAgentInvoker):
    """Force every development analysis run to request changes."""

    def __init__(self, workspace: MemoryWorkspace) -> None:
        super().__init__(workspace)
        self.last_phase: str | None = None

    def invoke(self, agent_name: str, phase: str) -> PipelineEvent:
        self.last_phase = phase
        return super().invoke(agent_name, phase)

    def analysis_event_for(self, phase: str) -> PipelineEvent:
        if phase == "development_analysis":
            return PipelineEvent.ANALYSIS_LOOPBACK
        return PipelineEvent.ANALYSIS_SUCCESS
