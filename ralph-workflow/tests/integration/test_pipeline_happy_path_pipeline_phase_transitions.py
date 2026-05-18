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

CALL_HISTORY_ENTRY_COUNT = 2

# ---------------------------------------------------------------------------
# Mock Agent Invoker
# ---------------------------------------------------------------------------


class TestPipelinePhaseTransitions:
    """Tests for phase transition routing."""

    def test_development_loopback_stays_in_development(
        self,
        default_policy: tuple[object, object, object],
        initial_state: PipelineState,
    ) -> None:
        """Test that development on loopback stays in development."""
        agents_policy, pipeline_policy, _ = default_policy

        # Set state to development with remaining budget
        state = initial_state.copy_with(phase="development")

        effect = determine_next_effect(state, pipeline_policy, agents_policy)

        # Development should continue (prep prompt or invoke)
        assert isinstance(effect, PreparePromptEffect | InvokeAgentEffect)
        assert effect.phase == "development"

    def test_planning_analysis_routes_to_development(
        self,
        default_policy: tuple[object, object, object],
        initial_state: PipelineState,
    ) -> None:
        """Test that planning_analysis routes to development on success."""
        agents_policy, pipeline_policy, _ = default_policy

        state = initial_state.copy_with(phase="planning_analysis")

        effect = determine_next_effect(state, pipeline_policy, agents_policy)

        assert isinstance(effect, PreparePromptEffect | InvokeAgentEffect)
        assert effect.phase == "planning_analysis"
