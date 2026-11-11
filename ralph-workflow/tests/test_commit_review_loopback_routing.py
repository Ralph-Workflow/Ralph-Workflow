"""Black-box proof for the commit-review unsafe-action loopback routing.

Split from ``tests/test_commit_review_verify_gate.py`` so both files stay
under the repository's 1000-line file cap enforced by
:mod:`ralph.testing.audit_repo_structure`.
"""

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
    """The loopback route returns to the review phase; it never advances.

    Both commit-review phases declare the loopback route back onto
    themselves, so an unsafe action re-enters the review instead of moving
    the run on to the commit phase.
    """
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
