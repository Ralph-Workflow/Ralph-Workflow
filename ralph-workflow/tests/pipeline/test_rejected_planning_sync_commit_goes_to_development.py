from __future__ import annotations

from ralph.pipeline.run_loop import commit_route_was_rejected
from ralph.pipeline.state import PipelineState

_PROPOSAL = "refs/ralph/proposals/run/1-1"


def test_a_rejection_of_the_planning_sync_commit_is_recognised() -> None:
    before = PipelineState(phase="development_commit_review")
    after = PipelineState(phase="development", rollback_pending=True, rollback_ref=_PROPOSAL)

    assert commit_route_was_rejected(before, after)


def test_an_approved_commit_is_not_a_rejection() -> None:
    before = PipelineState(phase="development_commit_review")
    after = PipelineState(phase="development_commit")

    assert not commit_route_was_rejected(before, after)


def test_a_rollback_already_pending_before_the_step_is_not_a_new_rejection() -> None:
    pending = PipelineState(phase="planning", rollback_pending=True, rollback_ref=_PROPOSAL)

    assert not commit_route_was_rejected(pending, pending)
