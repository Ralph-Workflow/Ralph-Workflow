"""Structured validation for typed non-plan artifact payloads.

Covers: issues, fix_result, and analysis decision artifacts.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Collection

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

_ISSUE_SEVERITIES = frozenset({"high", "medium", "low"})
_ISSUES_STATUSES = frozenset({"issues_found", "no_issues"})


def _load_analysis_decision_vocabulary() -> frozenset[str]:
    """Load allowed analysis decision statuses from the bundled default artifacts.toml."""
    defaults = Path(__file__).parent.parent.parent / "policy" / "defaults" / "artifacts.toml"
    with defaults.open("rb") as f:
        data: dict[str, object] = tomllib.load(f)
    artifacts_obj = data.get("artifacts", {})
    if not isinstance(artifacts_obj, dict):
        return frozenset()
    artifacts = cast("dict[str, object]", artifacts_obj)
    vocab: set[str] = set()
    for contract_obj in artifacts.values():
        if not isinstance(contract_obj, dict):
            continue
        contract = cast("dict[str, object]", contract_obj)
        drain = contract.get("drain", "")
        if not isinstance(drain, str) or not drain.endswith("_analysis"):
            continue
        raw_vocab = contract.get("decision_vocabulary", [])
        if isinstance(raw_vocab, list):
            vocab.update(str(v) for v in raw_vocab)
    return frozenset(v for v in vocab if v)


_ANALYSIS_DECISION_VOCABULARY: frozenset[str] = _load_analysis_decision_vocabulary()


class _IssueEntry(BaseModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1)
    severity: Literal["high", "medium", "low"]
    summary: str = Field(..., min_length=1)


class Issues(BaseModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    model_config = ConfigDict(extra="forbid")

    status: Literal["issues_found", "no_issues"]
    summary: str = Field(..., min_length=1)
    issues: list[_IssueEntry] = Field(default_factory=list)
    what_came_up_short: list[str] = Field(default_factory=list)
    how_to_fix: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_remediation_when_issues_found(self) -> Issues:
        if self.status == "issues_found":
            if not self.issues:
                raise ValueError(
                    'issues must be non-empty when status is "issues_found"'
                )
            if not self.what_came_up_short:
                raise ValueError(
                    'what_came_up_short must be non-empty when status is "issues_found"'
                )
            if not self.how_to_fix:
                raise ValueError(
                    'how_to_fix must be non-empty when status is "issues_found"'
                )
        return self


class FixResult(BaseModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(..., min_length=1)
    files_changed: str = Field(..., min_length=1)
    next_steps: str | None = None


class AnalysisDecision(BaseModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Validation model for analysis decision artifacts.

    Enforces the documented artifact contract from format_docs/:
    - status must be one of the values in decision_vocabulary from the default artifacts policy
    - what_came_up_short and how_to_fix are required when status is
      "request_changes" or "failed", and must be omitted when "completed"
    """

    model_config = ConfigDict(extra="forbid")

    status: str
    summary: str = Field(..., min_length=1)
    what_came_up_short: list[str] | None = None
    how_to_fix: list[str] | None = None

    @model_validator(mode="after")
    def _check_status_and_remediation(self) -> AnalysisDecision:
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


def normalize_analysis_decision_content(
    content: dict[str, object],
    *,
    allowed_statuses: Collection[str] | None = None,
) -> dict[str, object]:
    normalized = _validate(AnalysisDecision, content)
    statuses = (
        frozenset(allowed_statuses)
        if allowed_statuses is not None
        else _ANALYSIS_DECISION_VOCABULARY
    )
    status = normalized.get("status")
    if not isinstance(status, str) or status not in statuses:
        allowed = sorted(statuses)
        raise TypedArtifactValidationError(f"status must be one of {allowed}")
    return normalized


__all__ = [
    "_ANALYSIS_DECISION_VOCABULARY",
    "TypedArtifactValidationError",
    "normalize_analysis_decision_content",
    "normalize_fix_result_content",
    "normalize_issues_content",
]
