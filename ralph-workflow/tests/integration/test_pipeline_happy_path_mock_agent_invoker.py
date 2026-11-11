from __future__ import annotations

from ralph.pipeline.events import PipelineEvent
from ralph.workspace.memory import MemoryWorkspace
from tests.integration._mock_agent_invoker import MockAgentInvoker

CALL_HISTORY_ENTRY_COUNT = 2


class TestMockAgentInvoker:
    def test_planning_returns_agent_success(self) -> None:
        invoker = MockAgentInvoker(MemoryWorkspace())
        event = invoker.invoke("claude", "planning")
        assert event == PipelineEvent.AGENT_SUCCESS

    def test_development_returns_agent_success(self) -> None:
        invoker = MockAgentInvoker(MemoryWorkspace())
        event = invoker.invoke("claude", "development")
        assert event == PipelineEvent.AGENT_SUCCESS

    def test_analysis_returns_analysis_success(self) -> None:
        invoker = MockAgentInvoker(MemoryWorkspace())

        event = invoker.invoke("claude", "development_analysis")
        assert event == PipelineEvent.ANALYSIS_SUCCESS

    def test_commit_returns_commit_success(self) -> None:
        invoker = MockAgentInvoker(MemoryWorkspace())

        event = invoker.invoke("claude", "development_commit")
        assert event == PipelineEvent.COMMIT_SUCCESS

    def test_commit_review_returns_agent_success(self) -> None:
        invoker = MockAgentInvoker(MemoryWorkspace())

        event = invoker.invoke("claude", "development_commit_review")
        assert event == PipelineEvent.AGENT_SUCCESS

    def test_invoker_records_call_history(self) -> None:
        invoker = MockAgentInvoker(MemoryWorkspace())

        invoker.invoke("claude", "planning")
        invoker.invoke("claude", "development")

        assert len(invoker.call_history) == CALL_HISTORY_ENTRY_COUNT
        assert invoker.call_history[0]["phase"] == "planning"
        assert invoker.call_history[1]["phase"] == "development"
