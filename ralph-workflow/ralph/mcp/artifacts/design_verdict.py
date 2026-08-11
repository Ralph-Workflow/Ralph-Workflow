"""Structured ``design_verdict`` artifact validation helpers.

The pydantic schema mirrors the markdown-spec mapper output: the
provenance fields, the verbatim intent, the verdict status /
summary, and the list of findings. The cross-section invariants
(capture_id must appear in cell_ids, status must match the
findings, intent must not smuggle source-reading phrases) are
enforced by the markdown spec's ``validate_document`` hook
because they need the parsed-side ordering; the schema here only
checks the structural shape so a downstream consumer can rely
on every field being present and well typed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, ValidationError, model_validator

from ralph.mcp.multimodal.resources import parse_media_uri
from ralph.pydantic_compat import RalphBaseModel
from ralph.pydantic_validation_errors import format_validation_error_messages

DESIGN_VERDICT_ARTIFACT_TYPE = "design_verdict"

_STATUSES: tuple[Literal["pass", "fail", "blocked"], ...] = ("pass", "fail", "blocked")
_JUDGEMENT_TIERS: tuple[Literal["deterministic", "on-demand"], ...] = (
    "deterministic",
    "on-demand",
)


class DesignVerdict(RalphBaseModel):
    """Validated schema for a ``design_verdict`` artifact payload."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(..., min_length=1)
    judgement_tier: str | None = None
    verdict_id: str | None = None
    target: str = Field(..., min_length=1)
    before_id: str = Field(..., min_length=1)
    after_id: str = Field(..., min_length=1)
    cell_ids: list[str] = Field(..., min_length=1)
    before_handles: tuple[str, ...] = Field(default_factory=tuple)
    after_handles: tuple[str, ...] = Field(default_factory=tuple)
    intent: str = Field(..., min_length=1)
    status: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)
    findings: list[dict[str, object]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_status_vocabulary(self) -> DesignVerdict:
        if self.status not in _STATUSES:
            msg = f"status must be one of {list(_STATUSES)!r}"
            raise ValueError(msg)
        if self.judgement_tier is not None and self.judgement_tier not in _JUDGEMENT_TIERS:
            msg = f"judgement_tier must be one of {list(_JUDGEMENT_TIERS)!r}"
            raise ValueError(msg)
        for handle in (*self.before_handles, *self.after_handles):
            if parse_media_uri(handle) is None:
                raise ValueError(
                    f"capture handle {handle!r} must be a ralph://media/{{artifact_id}} URI"
                )
        return self


def normalize_design_verdict_content(content: dict[str, object]) -> dict[str, object]:
    """Validate and normalize a raw ``design_verdict`` content dict."""
    try:
        validated = DesignVerdict.model_validate(content)
    except ValidationError as exc:
        msgs = format_validation_error_messages(exc)
        raise ValueError(
            msgs[0] if len(msgs) == 1 else "\n".join(msgs) if msgs else str(exc)
        ) from exc
    return validated.model_dump(mode="python", exclude_none=True)


__all__ = [
    "DESIGN_VERDICT_ARTIFACT_TYPE",
    "DesignVerdict",
    "normalize_design_verdict_content",
]
