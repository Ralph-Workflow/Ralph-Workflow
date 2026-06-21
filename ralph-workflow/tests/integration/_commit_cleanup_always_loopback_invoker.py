"""Canonical invoker that forces every commit cleanup run to loopback."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.events import PipelineEvent
from tests.integration._mock_agent_invoker import MockAgentInvoker

if TYPE_CHECKING:
    from ralph.workspace.memory import MemoryWorkspace


class CommitCleanupAlwaysLoopbackInvoker(MockAgentInvoker):
    """Force every commit cleanup run to loopback."""

    def __init__(self, workspace: MemoryWorkspace) -> None:
        super().__init__(workspace)
        self.last_phase: str | None = None

    def invoke(self, agent_name: str, phase: str) -> PipelineEvent:
        self.last_phase = phase
        return super().invoke(agent_name, phase)

    def commit_event_for(self, phase: str) -> PipelineEvent:
        if phase in {
            "development_commit_cleanup",
            "development_final_commit_cleanup",
        }:
            return PipelineEvent.PHASE_LOOPBACK
        return super().commit_event_for(phase)
