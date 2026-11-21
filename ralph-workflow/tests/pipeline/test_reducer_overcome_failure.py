from __future__ import annotations

import pytest

from ralph.phases.commit_review import CommitReviewApproved, CommitReviewLoopback
from ralph.pipeline.events import PipelineEvent, PublicationGateFailed
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import cached_default_policy_bundle

_EARLIER = "Publication gate `make verify` failed (exit 1); the run's commits were not published."


@pytest.mark.parametrize(
    ("phase", "event", "next_phase"),
    [
        pytest.param(
            "development", PipelineEvent.AGENT_SUCCESS, "development_commit_review", id="agent"
        ),
        pytest.param(
            "development_commit_review",
            CommitReviewApproved(tree_sha="d" * 40),
            "development_commit",
            id="review-approved",
        ),
    ],
)
def test_a_phase_that_succeeds_no_longer_reports_the_failure_it_overcame(
    phase: str, event: object, next_phase: str
) -> None:
    state = PipelineState(phase=phase, last_error=_EARLIER)

    reduced, _ = reduce(state, event, cached_default_policy_bundle().pipeline)

    assert (reduced.phase, reduced.last_error) == (next_phase, None)


@pytest.mark.parametrize(
    ("phase", "event", "cause"),
    [
        pytest.param(
            "development_commit_review",
            CommitReviewLoopback(hint="re-review the current tree"),
            "re-review the current tree",
            id="review-loopback",
        ),
        pytest.param(
            "development_commit",
            PublicationGateFailed(phase="development_commit", detail="gate red"),
            "gate red",
            id="gate-route-back",
        ),
    ],
)
def test_a_route_back_keeps_the_cause_that_sent_the_run_back(
    phase: str, event: object, cause: str
) -> None:
    state = PipelineState(phase=phase, last_error=_EARLIER)

    reduced, _ = reduce(state, event, cached_default_policy_bundle().pipeline)

    assert reduced.last_error == cause
