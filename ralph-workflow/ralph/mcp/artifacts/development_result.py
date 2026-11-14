from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, ValidationError, model_validator

from ralph.mcp.artifacts.development_result_validation_error import DevelopmentResultValidationError
from ralph.pydantic_compat import RalphBaseModel
from ralph.pydantic_validation_errors import format_validation_error_messages

DEVELOPMENT_RESULT_ARTIFACT_TYPE = "development_result"


class DevelopmentResult(RalphBaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str = Field(..., min_length=1)
    summary: str = ""
    files_changed: str = ""
    next_steps: str | None = None
    continuation: dict[str, object] | None = None
    unplanned_work: list[str] = Field(default_factory=list)
    incomplete_work: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_requirements(self) -> DevelopmentResult:
        allowed_statuses: tuple[Literal["completed", "partial", "failed"], ...] = (
            "completed",
            "partial",
            "failed",
        )
        if self.status not in allowed_statuses:
            msg = f"status must be one of {list(allowed_statuses)!r}"
            raise ValueError(msg)
        return self


def normalize_development_result_content(content: dict[str, object]) -> dict[str, object]:
    try:
        validated = DevelopmentResult.model_validate(content)
    except ValidationError as exc:
        msgs = format_validation_error_messages(exc)
        raise DevelopmentResultValidationError(
            msgs[0] if len(msgs) == 1 else "\n".join(msgs) if msgs else str(exc)
        ) from exc
    dumped = validated.model_dump(mode="python", exclude_none=True)
    for key in ("summary", "files_changed"):
        if dumped.get(key) == "":
            dumped.pop(key, None)
    for key in ("incomplete_work", "unplanned_work"):
        if not dumped.get(key):
            dumped.pop(key, None)
    return dumped


__all__ = [
    "DEVELOPMENT_RESULT_ARTIFACT_TYPE",
    "DevelopmentResult",
    "DevelopmentResultValidationError",
    "normalize_development_result_content",
]
