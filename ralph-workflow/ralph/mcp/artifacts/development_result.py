"""Structured development_result artifact validation helpers.

The development_result artifact is free-form below the frontmatter:
routing reads only the closed ``status`` vocabulary
(``completed`` | ``partial`` | ``failed``), and the next agent reads
whatever the developer writes. The earlier proof machinery
(``PlanItemProof``, ``AnalysisItemProof``, ``Incomplete Work`` CLOSED
grammar, design-verdict coupling, and ``ArtifactProofPolicy``) has been
removed; coverage of the plan and of the prior analysis is judged by
development analysis, not checked mechanically here.
"""

from __future__ import annotations

from typing import Literal, cast

from pydantic import ConfigDict, Field, ValidationError, model_validator

from ralph.mcp.artifacts.development_result_validation_error import DevelopmentResultValidationError
from ralph.pydantic_compat import RalphBaseModel
from ralph.pydantic_validation_errors import format_validation_error_messages

DEVELOPMENT_RESULT_ARTIFACT_TYPE = "development_result"


class DevelopmentResult(RalphBaseModel):
    """Validated schema for a development_result artifact.

    ``status`` keeps its closed vocabulary because routing reads it.
    Everything below the frontmatter is free-form prose the next
    iteration reads, never a structure this validator gates.
    """

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
        """Enforce the routing vocabulary on ``status`` only.

        A non-``completed`` result carries no completion claim to
        check, so all body validation is skipped for it.
        """
        allowed_statuses: tuple[Literal["completed", "partial", "failed"], ...] = (
            "completed",
            "partial",
            "failed",
        )
        if self.status not in allowed_statuses:
            msg = f"status must be one of {list(cast('tuple[str, ...]', allowed_statuses))!r}"  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
            raise ValueError(msg)
        return self


def normalize_development_result_content(content: dict[str, object]) -> dict[str, object]:
    """Validate the routing vocabulary and pass the body through unchanged."""
    try:
        validated = DevelopmentResult.model_validate(content)
    except ValidationError as exc:
        msgs = format_validation_error_messages(exc)
        raise DevelopmentResultValidationError(
            msgs[0] if len(msgs) == 1 else "\n".join(msgs) if msgs else str(exc)
        ) from exc
    dumped = validated.model_dump(mode="python", exclude_none=True)
    # Free-form contract: drop empty defaults so a bare ``status`` payload
    # stays minimal. Continuation, next_steps, and lists are still kept
    # when set, since they may matter for continuation handoff.
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
