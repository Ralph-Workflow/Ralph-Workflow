from __future__ import annotations

from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from ralph.mcp.artifacts._commit_review_action import CommitReviewAction
from ralph.pydantic_compat import RalphBaseModel


class CommitReview(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["approve", "reject"]
    tree_sha: str = Field(..., pattern=r"^[0-9a-fA-F]{40}$")
    actions: list[CommitReviewAction] = Field(default_factory=list)
    reason: str | None = None

    @model_validator(mode="after")
    def _validate_reason_for_verdict(self) -> Self:
        if self.verdict == "reject" and (not self.reason or not self.reason.strip()):
            raise ValueError("reason is required when verdict is 'reject'")
        return self
