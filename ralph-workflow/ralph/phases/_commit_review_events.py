"""Events emitted by the commit-review phase."""

from __future__ import annotations

from ralph.phases._commit_review_approved import CommitReviewApproved
from ralph.phases._commit_review_gate_failed import CommitReviewGateFailed
from ralph.phases._commit_review_loopback import CommitReviewLoopback
from ralph.phases._commit_review_rejected import CommitReviewRejected

CommitReviewEvent = (
    CommitReviewApproved | CommitReviewRejected | CommitReviewGateFailed | CommitReviewLoopback
)

__all__ = [
    "CommitReviewApproved",
    "CommitReviewEvent",
    "CommitReviewGateFailed",
    "CommitReviewLoopback",
    "CommitReviewRejected",
]
