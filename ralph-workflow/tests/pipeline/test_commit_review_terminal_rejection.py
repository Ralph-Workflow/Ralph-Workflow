from __future__ import annotations

import functools
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.phases.commit_review import CommitReviewRejected
from ralph.pipeline.cycle_timing import RoutingTiming
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy

_DEFAULTS_DIR = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"
_REVIEW_PHASE = "development_commit_review"


@functools.cache
def _policy() -> PipelinePolicy:
    return load_policy(_DEFAULTS_DIR).pipeline


@pytest.mark.parametrize("terminal_condition", ("rejection_limit", "cycle_timebox"))
def test_wu_a2_terminal_regression_terminal_rejection_retains_saved_proposal_without_rollback(
    terminal_condition: str,
) -> None:
    policy = _policy()
    ref = f"refs/ralph/proposals/wu-a2-terminal/{terminal_condition}"
    if terminal_condition == "rejection_limit":
        max_rejections = policy.loop_counters["commit_review_rejections"].default_max
        state = PipelineState(
            phase=_REVIEW_PHASE,
            commit_review_rejections=max_rejections - 1,
        )
        elapsed_seconds = 1.0
    else:
        assert policy.cycle_timebox is not None
        state = PipelineState(
            phase=_REVIEW_PHASE,
            cycle_timebox_active=True,
            commit_review_rejections=0,
        )
        elapsed_seconds = float(policy.cycle_timebox.duration_seconds) + 1.0

    rejected, effects = reduce(
        state,
        CommitReviewRejected(reason="proposal rejected", ref=ref, hard=True),
        policy,
        routing_timing=RoutingTiming(total_elapsed_seconds=elapsed_seconds),
    )

    assert rejected.phase == policy.recovery.failed_route
    assert rejected.rollback_pending is False
    assert rejected.rollback_ref == ref
    assert ref in (rejected.last_error or "")
    assert effects == []


def test_wu_a2_terminal_regression_normal_rejection_still_requests_development_rollback() -> None:
    policy = _policy()
    ref = "refs/ralph/proposals/wu-a2-terminal/normal"

    rejected, effects = reduce(
        PipelineState(phase=_REVIEW_PHASE, commit_review_rejections=0),
        CommitReviewRejected(reason="proposal rejected", ref=ref, hard=True),
        policy,
        routing_timing=RoutingTiming(total_elapsed_seconds=1.0),
    )

    assert rejected.phase == "development"
    assert rejected.rollback_pending is True
    assert rejected.rollback_ref == ref
    assert effects == []
