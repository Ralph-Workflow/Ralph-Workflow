"""AnalysisDecision — validation model for analysis decision artifacts."""

from __future__ import annotations

from typing import Self

from pydantic import ConfigDict, Field, model_validator

from ralph.pydantic_compat import RalphBaseModel


class AnalysisDecision(RalphBaseModel):
    """Validation model for analysis decision artifacts."""

    model_config = ConfigDict(extra="forbid")

    status: str
    summary: str = Field(..., min_length=1)
    what_came_up_short: list[str] | None = None
    how_to_fix: list[str] | None = None

    @model_validator(mode="after")
    def _check_status_and_remediation(self) -> Self:
        if self.status in ("request_changes", "failed"):
            if not self.what_came_up_short:
                raise ValueError(
                    f'what_came_up_short is required when status is "{self.status}"'
                )
            if not self.how_to_fix:
                raise ValueError(f'how_to_fix is required when status is "{self.status}"')
        return self


__all__ = ["AnalysisDecision"]
