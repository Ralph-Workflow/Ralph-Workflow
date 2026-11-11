from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.effects import InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.orchestrator import determine_next_effect

if TYPE_CHECKING:
    from ralph.pipeline.state import PipelineState

CALL_HISTORY_ENTRY_COUNT = 2


class TestPipelinePhaseTransitions:
    def test_development_loopback_stays_in_development(
        self,
        default_policy: tuple[object, object, object],
        initial_state: PipelineState,
    ) -> None:
        agents_policy, pipeline_policy, _ = default_policy

        state = initial_state.copy_with(phase="development")

        effect = determine_next_effect(state, pipeline_policy, agents_policy)

        assert isinstance(effect, PreparePromptEffect | InvokeAgentEffect)
        assert effect.phase == "development"

    def test_planning_analysis_routes_to_development(
        self,
        default_policy: tuple[object, object, object],
        initial_state: PipelineState,
    ) -> None:
        agents_policy, pipeline_policy, _ = default_policy

        state = initial_state.copy_with(phase="planning_analysis")

        effect = determine_next_effect(state, pipeline_policy, agents_policy)

        assert isinstance(effect, PreparePromptEffect | InvokeAgentEffect)
        assert effect.phase == "planning_analysis"
