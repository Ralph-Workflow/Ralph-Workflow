"""Structured validation for typed non-plan artifact payloads.

Covers: issues, fix_result, and analysis decision artifacts.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self, cast

from pydantic import ConfigDict, Field, ValidationError, model_validator

from ralph.mcp.artifacts._analysis_decision import AnalysisDecision
from ralph.mcp.artifacts._commit_cleanup import CommitCleanup
from ralph.mcp.artifacts._fix_result import FixResult
from ralph.mcp.artifacts._issue_entry import _IssueEntry
from ralph.mcp.artifacts._typed_artifact_validation_error import TypedArtifactValidationError
from ralph.pydantic_compat import RalphBaseModel

if TYPE_CHECKING:
    from collections.abc import Collection


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


class Issues(RalphBaseModel):
    """Validated schema for an issues artifact payload."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["issues_found", "no_issues"]
    summary: str = Field(..., min_length=1)
    issues: list[_IssueEntry] = Field(default_factory=list)
    what_came_up_short: list[str] = Field(default_factory=list)
    how_to_fix: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_remediation_when_issues_found(self) -> Self:
        if self.status == "issues_found":
            if not self.issues:
                raise ValueError('issues must be non-empty when status is "issues_found"')
            if not self.what_came_up_short:
                raise ValueError(
                    'what_came_up_short must be non-empty when status is "issues_found"'
                )
            if not self.how_to_fix:
                raise ValueError('how_to_fix must be non-empty when status is "issues_found"')
        return self


def _validate(model_cls: type[RalphBaseModel], content: dict[str, object]) -> dict[str, object]:
    try:
        validated = model_cls.model_validate(content)
        return validated.model_dump(mode="python", exclude_none=True)
    except ValidationError as exc:
        raise TypedArtifactValidationError(str(exc)) from exc


def normalize_issues_content(content: dict[str, object]) -> dict[str, object]:
    """Validate and normalize a raw issues artifact content dict."""
    return _validate(Issues, content)


def normalize_fix_result_content(content: dict[str, object]) -> dict[str, object]:
    """Validate and normalize a raw fix_result artifact content dict."""
    return _validate(FixResult, content)


def normalize_analysis_decision_content(
    content: dict[str, object],
    *,
    allowed_statuses: Collection[str] | None = None,
) -> dict[str, object]:
    """Validate and normalize an analysis decision artifact content dict."""
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


def normalize_commit_cleanup_content(content: dict[str, object]) -> dict[str, object]:
    """Validate and normalize a raw commit_cleanup artifact content dict."""
    return _validate(CommitCleanup, content)


__all__ = [
    "_ANALYSIS_DECISION_VOCABULARY",
    "TypedArtifactValidationError",
    "normalize_analysis_decision_content",
    "normalize_commit_cleanup_content",
    "normalize_fix_result_content",
    "normalize_issues_content",
]
