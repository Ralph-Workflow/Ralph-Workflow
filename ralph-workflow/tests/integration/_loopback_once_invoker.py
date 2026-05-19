from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.events import PipelineEvent
from tests.integration._mock_agent_invoker import MockAgentInvoker

if TYPE_CHECKING:
    from ralph.workspace.memory import MemoryWorkspace


class LoopbackOnceInvoker(MockAgentInvoker):
    """Return a single development-analysis loopback before succeeding."""

    def __init__(self, workspace: MemoryWorkspace) -> None:
        super().__init__(workspace)
        self._development_analysis_calls = 0
        self.last_phase: str | None = None

    def invoke(self, agent_name: str, phase: str) -> PipelineEvent:
        self.last_phase = phase
        return super().invoke(agent_name, phase)

    def analysis_event_for(self, phase: str) -> PipelineEvent:
        if phase == "development_analysis":
            self._development_analysis_calls += 1
            if self._development_analysis_calls == 1:
                return PipelineEvent.ANALYSIS_LOOPBACK
        return PipelineEvent.ANALYSIS_SUCCESS
