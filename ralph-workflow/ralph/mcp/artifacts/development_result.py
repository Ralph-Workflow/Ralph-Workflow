"""Structured development_result artifact validation helpers."""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

DEVELOPMENT_RESULT_ARTIFACT_TYPE = "development_result"


class DevelopmentResultValidationError(ValueError):
    """Raised when a development_result artifact is malformed."""


class Continuation(BaseModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    model_config = ConfigDict(extra="forbid")

    prior_session_id: str = Field(..., min_length=1)


class DevelopmentResult(BaseModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
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
    try:
        validated = DevelopmentResult.model_validate(content)
        return cast(
            "dict[str, object]",
            validated.model_dump(mode="python", exclude_none=True),
        )
    except ValidationError as exc:
        raise DevelopmentResultValidationError(str(exc)) from exc


__all__ = [
    "DEVELOPMENT_RESULT_ARTIFACT_TYPE",
    "DevelopmentResultValidationError",
    "normalize_development_result_content",
]
