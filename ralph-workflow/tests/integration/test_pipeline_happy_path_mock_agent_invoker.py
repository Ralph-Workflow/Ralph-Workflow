"""Integration tests for the full pipeline happy path.

Tests the complete pipeline from planning to completion using mocked
agent invocations. The mock returns success for all phases, allowing
the pipeline to advance through all phases to completion.
"""

from __future__ import annotations

from ralph.pipeline.events import PipelineEvent
from ralph.workspace.memory import MemoryWorkspace
from tests.integration._mock_agent_invoker import MockAgentInvoker

CALL_HISTORY_ENTRY_COUNT = 2

# ---------------------------------------------------------------------------
# Mock Agent Invoker
# ---------------------------------------------------------------------------


class TestMockAgentInvoker:
    """Tests for the MockAgentInvoker class."""

    def test_planning_returns_agent_success(self) -> None:
        """Test that planning phase returns AGENT_SUCCESS."""
        invoker = MockAgentInvoker(MemoryWorkspace())
        event = invoker.invoke("claude", "planning")
        assert event == PipelineEvent.AGENT_SUCCESS

    def test_development_returns_agent_success(self) -> None:
        """Test that development phase returns AGENT_SUCCESS."""
        invoker = MockAgentInvoker(MemoryWorkspace())
        event = invoker.invoke("claude", "development")
        assert event == PipelineEvent.AGENT_SUCCESS

    def test_analysis_returns_analysis_success(self) -> None:
        """Test that analysis phases return ANALYSIS_SUCCESS."""
        invoker = MockAgentInvoker(MemoryWorkspace())

        event = invoker.invoke("claude", "development_analysis")
        assert event == PipelineEvent.ANALYSIS_SUCCESS

    def test_commit_returns_commit_success(self) -> None:
        """Test that commit phases return COMMIT_SUCCESS."""
        invoker = MockAgentInvoker(MemoryWorkspace())

        event = invoker.invoke("claude", "development_commit")
        assert event == PipelineEvent.COMMIT_SUCCESS

    def test_commit_cleanup_returns_agent_success(self) -> None:
        """Test that commit_cleanup phases return AGENT_SUCCESS (not COMMIT_SUCCESS)."""
        invoker = MockAgentInvoker(MemoryWorkspace())

        event = invoker.invoke("claude", "development_commit_cleanup")
        assert event == PipelineEvent.AGENT_SUCCESS

    def test_invoker_records_call_history(self) -> None:
        """Test that invoker records the call history."""
        invoker = MockAgentInvoker(MemoryWorkspace())

        invoker.invoke("claude", "planning")
        invoker.invoke("claude", "development")

        assert len(invoker.call_history) == CALL_HISTORY_ENTRY_COUNT
        assert invoker.call_history[0]["phase"] == "planning"
        assert invoker.call_history[1]["phase"] == "development"
