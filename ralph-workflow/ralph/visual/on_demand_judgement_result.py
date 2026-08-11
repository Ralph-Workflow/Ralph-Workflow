"""Result contract for one on-demand visual judgement request."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OnDemandJudgementResult:
    """The submitted verdict reference or actionable blocker from review."""

    verdict_id: str | None = None
    status: str | None = None
    blocker: str | None = None

    def __post_init__(self) -> None:
        submitted = self.verdict_id is not None or self.status is not None
        if submitted == (self.blocker is not None):
            raise ValueError("on-demand judgement must return a verdict or an actionable blocker")
        if submitted and (not self.verdict_id or not self.status):
            raise ValueError("submitted on-demand judgement requires verdict_id and status")
        if self.blocker is not None and not self.blocker.strip():
            raise ValueError("on-demand judgement blocker must be non-empty")
