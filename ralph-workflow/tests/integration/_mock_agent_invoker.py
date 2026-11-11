from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ralph.pipeline.events import PipelineEvent

if TYPE_CHECKING:
    from ralph.workspace.memory import MemoryWorkspace


class MockAgentInvoker:
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
            return PipelineEvent.AGENT_SUCCESS

        if "analysis" in phase:
            return PipelineEvent.ANALYSIS_SUCCESS

        if phase.endswith("_commit") or phase == "commit":
            return PipelineEvent.COMMIT_SUCCESS

        return PipelineEvent.AGENT_SUCCESS

    def count_for(self, phase: str) -> int:
        return self.call_counts.get(phase, 0)

    def commit_event_for(self, phase: str) -> PipelineEvent:
        if phase.endswith("_commit") or phase == "commit":
            return PipelineEvent.COMMIT_SUCCESS
        return PipelineEvent.AGENT_SUCCESS
