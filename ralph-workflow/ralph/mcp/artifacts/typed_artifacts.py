"""Structured validation for typed non-plan artifact payloads.

Covers: issues, fix_result, development_analysis_decision, review_analysis_decision.
"""

from __future__ import annotations

from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

_ISSUE_SEVERITIES = frozenset({"high", "medium", "low"})
_ISSUES_STATUSES = frozenset({"issues_found", "no_issues"})


class _IssueEntry(BaseModel):  # type: ignore[explicit-any]
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1)
    severity: Literal["high", "medium", "low"]
    summary: str = Field(..., min_length=1)


class Issues(BaseModel):  # type: ignore[explicit-any]
    model_config = ConfigDict(extra="forbid")

    status: Literal["issues_found", "no_issues"]
    summary: str = Field(..., min_length=1)
    issues: list[_IssueEntry]
    what_came_up_short: list[str]
    how_to_fix: list[str]


class FixResult(BaseModel):  # type: ignore[explicit-any]
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(..., min_length=1)
    files_changed: str = Field(..., min_length=1)
    next_steps: str | None = None


class AnalysisDecision(BaseModel):  # type: ignore[explicit-any]
    """Validation model for analysis decision artifacts.

    Enforces the documented artifact contract from format_docs/:
    - status must be "completed", "request_changes", or "failed"
    - what_came_up_short and how_to_fix are required when status is
      "request_changes" or "failed", and must be omitted when "completed"
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["completed", "request_changes", "failed"]
    summary: str = Field(..., min_length=1)
    what_came_up_short: list[str] | None = None
    how_to_fix: list[str] | None = None

    @model_validator(mode="after")
    def _check_remediation_fields(self) -> AnalysisDecision:
        if self.status in ("request_changes", "failed"):
            if not self.what_came_up_short:
                raise ValueError(
                    f'what_came_up_short is required when status is "{self.status}"'
                )
            if not self.how_to_fix:
                raise ValueError(
                    f'how_to_fix is required when status is "{self.status}"'
                )
        return self


class TypedArtifactValidationError(ValueError):
    """Raised when a typed artifact payload is malformed."""


def _validate(model_cls: type[BaseModel], content: dict[str, object]) -> dict[str, object]:
    try:
        validated = model_cls.model_validate(content)
        return cast("dict[str, object]", validated.model_dump(mode="python", exclude_none=True))
    except ValidationError as exc:
        raise TypedArtifactValidationError(str(exc)) from exc


def normalize_issues_content(content: dict[str, object]) -> dict[str, object]:
    return _validate(Issues, content)


def normalize_fix_result_content(content: dict[str, object]) -> dict[str, object]:
    return _validate(FixResult, content)


def normalize_analysis_decision_content(content: dict[str, object]) -> dict[str, object]:
    return _validate(AnalysisDecision, content)


__all__ = [
    "TypedArtifactValidationError",
    "normalize_analysis_decision_content",
    "normalize_fix_result_content",
    "normalize_issues_content",
]
