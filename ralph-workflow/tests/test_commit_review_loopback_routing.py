from __future__ import annotations

from pathlib import Path

import pytest

from ralph.phases.commit_review import CommitReviewLoopback
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

pytestmark = pytest.mark.timeout_seconds(5)

_DEFAULT_POLICY_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


def test_unsafe_action_loopback_returns_to_the_review_phase_instead_of_advancing() -> None:
    policy = load_policy(_DEFAULT_POLICY_DIR).pipeline
    for phase, commit_phase in (
        ("development_commit_review", "development_commit"),
        ("development_final_commit_review", "development_final_commit"),
    ):
        phase_def = policy.phases[phase]
        assert phase_def.transitions.on_loopback == phase, (
            f"{phase} must loop back onto itself, got: {phase_def.transitions.on_loopback!r}"
        )
        assert phase_def.transitions.on_success == commit_phase

        next_state, _effects = reducer_reduce(
            PipelineState(phase=phase),
            CommitReviewLoopback(hint="retry the artifact"),
            policy,
        )
        assert next_state.phase == phase, (
            f"{phase} must not advance on a loopback, got: {next_state.phase!r}"
        )
