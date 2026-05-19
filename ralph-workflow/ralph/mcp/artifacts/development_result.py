"""Structured development_result artifact validation helpers."""

from __future__ import annotations

from pydantic import ConfigDict, Field, ValidationError, model_validator

from ralph.mcp.artifacts.analysis_item_proof import AnalysisItemProof
from ralph.mcp.artifacts.development_result_continuation import Continuation
from ralph.mcp.artifacts.development_result_validation_error import DevelopmentResultValidationError
from ralph.mcp.artifacts.plan_item_proof import PlanItemProof
from ralph.pydantic_compat import RalphBaseModel

DEVELOPMENT_RESULT_ARTIFACT_TYPE = "development_result"


class DevelopmentResult(RalphBaseModel):
    """Validated schema for a development_result artifact."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    files_changed: str = Field(..., min_length=1)
    plan_items_proven: list[PlanItemProof] = Field(default_factory=list)
    analysis_items_addressed: list[AnalysisItemProof] = Field(default_factory=list)
    next_steps: str | None = None
    continuation: Continuation | None = None

    @model_validator(mode="after")
    def validate_status_requirements(self) -> DevelopmentResult:
        if self.status == "partial":
            if self.next_steps is None:
                raise ValueError("partial development_result artifacts require next_steps")
            if self.continuation is None:
                raise ValueError("partial development_result artifacts require continuation")
        return self


def normalize_development_result_content(content: dict[str, object]) -> dict[str, object]:
    """Validate and normalize a raw development_result content dict."""
    try:
        validated = DevelopmentResult.model_validate(content)
        return validated.model_dump(mode="python", exclude_none=True)
    except ValidationError as exc:
        raise DevelopmentResultValidationError(str(exc)) from exc


__all__ = [
    "DEVELOPMENT_RESULT_ARTIFACT_TYPE",
    "AnalysisItemProof",
    "Continuation",
    "DevelopmentResult",
    "DevelopmentResultValidationError",
    "PlanItemProof",
    "normalize_development_result_content",
]
