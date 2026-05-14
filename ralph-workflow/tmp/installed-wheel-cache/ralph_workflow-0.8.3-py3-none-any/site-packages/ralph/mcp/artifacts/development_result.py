"""Structured development_result artifact validation helpers."""

from __future__ import annotations

from pydantic import ConfigDict, Field, ValidationError, model_validator

from ralph.pydantic_compat import RalphBaseModel

DEVELOPMENT_RESULT_ARTIFACT_TYPE = "development_result"


class DevelopmentResultValidationError(ValueError):
    """Raised when a development_result artifact is malformed."""


class Continuation(RalphBaseModel):
    """Reference to a prior session when a development result is partial."""

    model_config = ConfigDict(extra="forbid")

    prior_session_id: str = Field(..., min_length=1)


class DevelopmentResult(RalphBaseModel):
    """Validated schema for a development_result artifact."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    files_changed: str = Field(..., min_length=1)
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
    "DevelopmentResultValidationError",
    "normalize_development_result_content",
]
