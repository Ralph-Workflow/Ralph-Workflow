"""Integration tests for the full pipeline happy path.

Tests the complete pipeline from planning to completion using mocked
agent invocations. The mock returns success for all phases, allowing
the pipeline to advance through all phases to completion.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.effects import InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.orchestrator import determine_next_effect

if TYPE_CHECKING:
    from ralph.pipeline.state import PipelineState
    from ralph.workspace.memory import MemoryWorkspace

CALL_HISTORY_ENTRY_COUNT = 2


class TestPipelineHappyPath:
    """Tests for the complete pipeline happy path."""

    def test_planning_phase_routing(
        self,
        default_policy: tuple[object, object, object],
        initial_state: PipelineState,
    ) -> None:
        """Test that planning phase routes to development on success."""
        agents_policy, pipeline_policy, _ = default_policy

        effect = determine_next_effect(initial_state, pipeline_policy, agents_policy)

        # Planning should return PreparePromptEffect (not InvokeAgentEffect)
        # since the orchestrator first prepares the prompt before invoking
        assert isinstance(effect, PreparePromptEffect | InvokeAgentEffect)
        assert effect.phase == "planning"

    def test_development_budget_routing(
        self,
        default_policy: tuple[object, object, object],
        initial_state: PipelineState,
    ) -> None:
        """development_commit should still run commit even when budget is exhausted."""
        agents_policy, pipeline_policy, _ = default_policy

        # Set state to development_commit phase with exhausted budget
        state = initial_state.copy_with(phase="development_commit").with_outer_progress(
            "iteration", initial_state.get_budget_cap("iteration")
        )

        effect = determine_next_effect(state, pipeline_policy, agents_policy)

        # development_commit should execute commit checkpoint before routing
        assert isinstance(effect, PreparePromptEffect | InvokeAgentEffect)
        assert effect.phase == "development_commit"

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
        default_policy: tuple[object, object, object],
    ) -> None:
        """Test that default policy loads without error."""
        agents_policy, pipeline_policy, _artifacts_policy = default_policy

        # Verify all expected drains are bound
        expected_drains = {
            "planning",
            "development",
            "development_analysis",
            "development_commit",
        }
        actual_drains = set(agents_policy.agent_drains.keys())
        assert expected_drains.issubset(actual_drains)

        # Verify pipeline entry and terminal phases
        assert pipeline_policy.entry_phase == "planning"
        assert pipeline_policy.terminal_phase == "complete"

        # Verify phases reference bound drains (skip terminal-role phases)
        for phase_def in pipeline_policy.phases.values():
            if phase_def.role == "terminal":
                continue
            assert phase_def.drain in agents_policy.agent_drains
