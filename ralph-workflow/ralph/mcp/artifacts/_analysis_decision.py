"""AnalysisDecision — validation model for analysis decision artifacts."""

from __future__ import annotations

from typing import Self

from pydantic import ConfigDict, Field, model_validator

from ralph.pydantic_compat import RalphBaseModel


class AnalysisDecision(RalphBaseModel):
    """Validation model for an evidence-backed analysis decision artifact."""

    model_config = ConfigDict(extra="forbid")

    status: str
    summary: str = Field(..., min_length=1)
    what_came_up_short: list[str] | None = None
    finding_ids: list[str] = Field(default_factory=list)
    finding_targets: dict[str, str] = Field(default_factory=dict)
    criterion_verdicts: list[str] | None = None
    criterion_verdict_ids: list[str] = Field(default_factory=list)
    how_to_fix: list[str] | None = None

    @model_validator(mode="after")
    def _check_status_and_findings(self) -> Self:
        if self.status == "completed" and self.what_came_up_short:
            raise ValueError(
                "what_came_up_short must be omitted when status is \"completed\"; "
                "known gaps require a non-completed status"
            )
        if self.status in ("request_changes", "failed") and not self.what_came_up_short:
            raise ValueError(f'what_came_up_short is required when status is "{self.status}"')
        return self


__all__ = ["AnalysisDecision"]
