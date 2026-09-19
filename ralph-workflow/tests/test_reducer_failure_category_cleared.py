"""Successful phase routing clears active failure categories."""

from __future__ import annotations

from ralph.pipeline.progress import advance_phase
from ralph.pipeline.state import PipelineState
from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy


def test_successful_phase_advance_clears_artifact_validation_category() -> None:
    policy = PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development", transitions=PhaseTransition(on_success="complete")
            ),
            "complete": PhaseDefinition(
                drain="complete", transitions=PhaseTransition(on_success="complete")
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
    )
    state = PipelineState(phase="development", last_failure_category="artifact_validation")

    advanced = advance_phase(state, "complete", policy=policy)

    assert advanced.last_failure_category is None
