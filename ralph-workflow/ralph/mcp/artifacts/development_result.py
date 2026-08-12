"""Structured development_result artifact validation helpers."""

from __future__ import annotations

from typing import Literal, cast

from pydantic import ConfigDict, Field, ValidationError, model_validator

from ralph.mcp.artifacts.analysis_item_proof import AnalysisItemProof
from ralph.mcp.artifacts.development_result_continuation import Continuation
from ralph.mcp.artifacts.development_result_validation_error import DevelopmentResultValidationError
from ralph.mcp.artifacts.plan_item_proof import PlanItemProof
from ralph.pydantic_compat import RalphBaseModel
from ralph.pydantic_validation_errors import format_validation_error_messages

DEVELOPMENT_RESULT_ARTIFACT_TYPE = "development_result"


class DevelopmentResult(RalphBaseModel):
    """Validated schema for a development_result artifact."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., min_length=1)
    summary: str = ""
    files_changed: str = ""
    plan_items_proven: list[PlanItemProof] = Field(default_factory=list)
    analysis_items_addressed: list[AnalysisItemProof] = Field(default_factory=list)
    next_steps: str | None = None
    continuation: Continuation | None = None

    @model_validator(mode="after")
    def validate_status_requirements(self) -> DevelopmentResult:
        """Enforce the reporting contract only for a ``completed`` result.

        ``status`` keeps its closed vocabulary because routing reads it,
        but a non-``completed`` result carries no completion claim to
        check: everything below the frontmatter is free-form prose the
        next iteration reads, never a structure this validator gates.
        """
        allowed_statuses: tuple[Literal["completed", "partial", "failed"], ...] = (
            "completed",
            "partial",
            "failed",
        )
        if self.status not in allowed_statuses:
            msg = f"status must be one of {list(cast('tuple[str, ...]', allowed_statuses))!r}"  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
            raise ValueError(msg)
        if self.status != "completed":
            return self
        if not self.summary.strip():
            raise ValueError("completed development_result artifacts require summary")
        if not self.files_changed.strip():
            raise ValueError("completed development_result artifacts require files_changed")
        blocked = [proof.plan_item for proof in self.plan_items_proven if proof.disposition == "blocked"]
        if blocked:
            raise ValueError(
                "completed development_result artifacts cannot contain blocked plan items; "
                f"submit status partial instead: {blocked}"
            )
        return self


def normalize_development_result_content(content: dict[str, object]) -> dict[str, object]:
    """Validate and normalize a raw development_result content dict."""
    try:
        validated = DevelopmentResult.model_validate(content)
        dumped = validated.model_dump(mode="python", exclude_none=True)
        # Strip empty tuple fields so the resulting dict matches the
        # pre-criterion-11 shape for non-UI plan items.
        for key in ("plan_items_proven", "analysis_items_addressed"):
            value = dumped.get(key)
            if isinstance(value, list):
                for entry in value:
                    if isinstance(entry, dict):
                        for default_field in ("capture_handles", "rationale", "verdict_id"):
                            if entry.get(default_field) in (None, ()):
                                entry.pop(default_field, None)
        return dumped
    except ValidationError as exc:
        msgs = format_validation_error_messages(exc)
        raise DevelopmentResultValidationError(
            msgs[0] if len(msgs) == 1 else "\n".join(msgs) if msgs else str(exc)
        ) from exc


__all__ = [
    "DEVELOPMENT_RESULT_ARTIFACT_TYPE",
    "AnalysisItemProof",
    "Continuation",
    "DevelopmentResult",
    "DevelopmentResultValidationError",
    "PlanItemProof",
    "normalize_development_result_content",
]
