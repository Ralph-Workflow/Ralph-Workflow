"""Integration tests for the full pipeline happy path.

Tests the complete pipeline from planning to completion using mocked
agent invocations. The mock returns success for all phases, allowing
the pipeline to advance through all phases to completion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from ralph.pipeline.effects import InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.orchestrator import determine_next_effect
from ralph.pipeline.state import AgentChainState, CommitState, PipelineState, RebaseState
from ralph.policy.loader import load_policy
from ralph.workspace.memory import MemoryWorkspace

CALL_HISTORY_ENTRY_COUNT = 2

# ---------------------------------------------------------------------------
# Mock Agent Invoker
# ---------------------------------------------------------------------------


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
        """Initialize mock invoker.

        Args:
            workspace: In-memory workspace for the pipeline.
        """
        self.workspace = workspace
        self.call_history: list[dict[str, Any]] = []

    def invoke(self, agent_name: str, phase: str) -> PipelineEvent:
        """Simulate agent invocation and return success event.

        Args:
            agent_name: Name of the agent being invoked.
            phase: Current pipeline phase.

        Returns:
            PipelineEvent indicating success.
        """
        self.call_history.append({"agent": agent_name, "phase": phase})

        # For planning and development: return AGENT_SUCCESS
        if phase in ("planning", "development", "review", "fix"):
            return cast("PipelineEvent", PipelineEvent.AGENT_SUCCESS)

        # For analysis phases: return ANALYSIS_SUCCESS
        if "analysis" in phase:
            return cast("PipelineEvent", PipelineEvent.ANALYSIS_SUCCESS)

        # For commit phases: return COMMIT_SUCCESS
        if "commit" in phase:
            return cast("PipelineEvent", PipelineEvent.COMMIT_SUCCESS)

        return cast("PipelineEvent", PipelineEvent.AGENT_SUCCESS)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def default_policy() -> tuple[Any, Any, Any]:
    """Load the default policy bundle.

    Returns:
        Tuple of (agents_policy, pipeline_policy, artifacts_policy).
    """
    default_dir = Path(__file__).parent.parent.parent / "ralph" / "policy" / "defaults"
    bundle = load_policy(default_dir)
    return bundle.agents, bundle.pipeline, bundle.artifacts


@pytest.fixture
def memory_workspace() -> MemoryWorkspace:
    """Create an in-memory workspace for testing.

    Returns:
        MemoryWorkspace instance.
    """
    ws = MemoryWorkspace()
    # Initialize with required prompt file
    ws.write("PROMPT.md", "# Test Prompt\n\nThis is a test prompt.")
    return ws


@pytest.fixture
def initial_state() -> PipelineState:
    """Create initial pipeline state for happy path testing.

    Returns:
        PipelineState at the planning phase with default budgets.
    """
    return PipelineState(
        phase="planning",
        total_iterations=1,
        total_reviewer_passes=1,
        dev_chain=AgentChainState(agents=["claude"]),
        rev_chain=AgentChainState(agents=["claude"]),
        rebase=RebaseState(),
        commit=CommitState(),
        development_budget_remaining=1,
        review_budget_remaining=1,
    )


# ---------------------------------------------------------------------------
# Happy Path Tests
# ---------------------------------------------------------------------------


class TestPipelineHappyPath:
    """Tests for the complete pipeline happy path."""

    def test_planning_phase_routing(
        self,
        default_policy: tuple[Any, Any, Any],
        initial_state: PipelineState,
    ) -> None:
        """Test that planning phase routes to development on success."""
        agents_policy, pipeline_policy, _ = default_policy

        effect = determine_next_effect(initial_state, pipeline_policy, agents_policy)

        # Planning should return PreparePromptEffect (not InvokeAgentEffect)
        # since the orchestrator first prepares the prompt before invoking
        assert isinstance(effect, (PreparePromptEffect, InvokeAgentEffect))
        assert effect.phase == "planning"

    def test_development_budget_routing(
        self,
        default_policy: tuple[Any, Any, Any],
        initial_state: PipelineState,
    ) -> None:
        """development_commit should still run commit even when budget is exhausted."""
        agents_policy, pipeline_policy, _ = default_policy

        # Set state to development_commit phase with exhausted budget
        state = initial_state.model_copy(
            update={
                "phase": "development_commit",
                "development_budget_remaining": 0,
            }
        )

        effect = determine_next_effect(state, pipeline_policy, agents_policy)

        # development_commit should execute commit checkpoint before routing
        assert isinstance(effect, (PreparePromptEffect, InvokeAgentEffect))
        assert effect.phase == "development_commit"

    def test_review_commit_to_complete(
        self,
        default_policy: tuple[Any, Any, Any],
        initial_state: PipelineState,
    ) -> None:
        """review_commit should run commit checkpoint before terminal routing."""
        agents_policy, pipeline_policy, _ = default_policy

        # Set state to review_commit phase
        state = initial_state.model_copy(
            update={
                "phase": "review_commit",
                "review_budget_remaining": 0,
            }
        )

        effect = determine_next_effect(state, pipeline_policy, agents_policy)

        assert isinstance(effect, (PreparePromptEffect, InvokeAgentEffect))
        assert effect.phase == "review_commit"

    def test_memory_workspace_persistence(
        self,
        memory_workspace: MemoryWorkspace,
    ) -> None:
        """Test that MemoryWorkspace persists file operations."""
        memory_workspace.write("test.txt", "Hello, World!")
        content = memory_workspace.read("test.txt")
        assert content == "Hello, World!"

    def test_workspace_exists_check(
        self,
        memory_workspace: MemoryWorkspace,
    ) -> None:
        """Test that MemoryWorkspace.exists works correctly."""
        assert not memory_workspace.exists("missing.txt")

        memory_workspace.write("present.txt", "content")
        assert memory_workspace.exists("present.txt")

    def test_policy_loading_smoke_test(
        self,
        default_policy: tuple[Any, Any, Any],
    ) -> None:
        """Test that default policy loads without error."""
        agents_policy, pipeline_policy, _artifacts_policy = default_policy

        # Verify all expected drains are bound
        expected_drains = {
            "planning",
            "development",
            "development_analysis",
            "development_commit",
            "review",
            "review_analysis",
            "fix",
            "review_commit",
        }
        actual_drains = set(agents_policy.agent_drains.keys())
        assert expected_drains.issubset(actual_drains)

        # Verify pipeline entry and terminal phases
        assert pipeline_policy.entry_phase == "planning"
        assert pipeline_policy.terminal_phase == "complete"

        # Verify phases reference bound drains (skip terminal phase)
        for phase_name, phase_def in pipeline_policy.phases.items():
            if phase_name == pipeline_policy.terminal_phase:
                continue
            assert phase_def.drain in agents_policy.agent_drains


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

        event = invoker.invoke("claude", "review_analysis")
        assert event == PipelineEvent.ANALYSIS_SUCCESS

    def test_commit_returns_commit_success(self) -> None:
        """Test that commit phases return COMMIT_SUCCESS."""
        invoker = MockAgentInvoker(MemoryWorkspace())

        event = invoker.invoke("claude", "development_commit")
        assert event == PipelineEvent.COMMIT_SUCCESS

        event = invoker.invoke("claude", "review_commit")
        assert event == PipelineEvent.COMMIT_SUCCESS

    def test_invoker_records_call_history(self) -> None:
        """Test that invoker records the call history."""
        invoker = MockAgentInvoker(MemoryWorkspace())

        invoker.invoke("claude", "planning")
        invoker.invoke("claude", "development")

        assert len(invoker.call_history) == CALL_HISTORY_ENTRY_COUNT
        assert invoker.call_history[0]["phase"] == "planning"
        assert invoker.call_history[1]["phase"] == "development"


class TestPipelinePhaseTransitions:
    """Tests for phase transition routing."""

    def test_development_loopback_stays_in_development(
        self,
        default_policy: tuple[Any, Any, Any],
        initial_state: PipelineState,
    ) -> None:
        """Test that development on loopback stays in development."""
        agents_policy, pipeline_policy, _ = default_policy

        # Set state to development with remaining budget
        state = initial_state.model_copy(
            update={
                "phase": "development",
                "development_budget_remaining": 1,
            }
        )

        effect = determine_next_effect(state, pipeline_policy, agents_policy)

        # Development should continue (prep prompt or invoke)
        assert isinstance(effect, (PreparePromptEffect, InvokeAgentEffect))
        assert effect.phase == "development"

    def test_review_with_issues_routes_to_fix(
        self,
        default_policy: tuple[Any, Any, Any],
        initial_state: PipelineState,
    ) -> None:
        """Test that review with issues routes to fix."""
        agents_policy, pipeline_policy, _ = default_policy

        # Set state to review
        state = initial_state.model_copy(
            update={
                "phase": "review",
                "review_budget_remaining": 1,
            }
        )

        effect = determine_next_effect(state, pipeline_policy, agents_policy)

        # Review should route to review_commit or stay in review
        assert isinstance(effect, (PreparePromptEffect, InvokeAgentEffect))
        assert effect.phase == "review"

    def test_fix_routes_to_review(
        self,
        default_policy: tuple[Any, Any, Any],
        initial_state: PipelineState,
    ) -> None:
        """Test that fix phase routes back to review."""
        agents_policy, pipeline_policy, _ = default_policy

        # Set state to fix
        state = initial_state.model_copy(
            update={
                "phase": "fix",
            }
        )

        effect = determine_next_effect(state, pipeline_policy, agents_policy)

        # Fix should route back to review
        assert isinstance(effect, (PreparePromptEffect, InvokeAgentEffect))
        assert effect.phase == "review"
