"""Evidence that a plan item was completed."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from pydantic import ConfigDict, Field, model_validator

from ralph.pydantic_compat import RalphBaseModel

if TYPE_CHECKING:
    from collections.abc import Mapping

# Regex used to identify UI plan items whose proof must cite a
# criterion 8 design verdict + capture handles (S-14 / criterion 11).
UI_LABEL_RE = re.compile(
    r"\b(?:ui|ux|visual|design|appearance|layout|screen|component)\b", re.IGNORECASE
)


class PlanItemProof(RalphBaseModel):
    """Evidence that a plan item was completed.

    For UI plan items (whose ``plan_item`` text matches
    :data:`UI_LABEL_RE`), the proof MUST cite a criterion 8 verdict
    id and at least one capture handle. Non-UI plan items keep the
    pre-criterion-11 contract (just plan_item + proof).
    """

    model_config = ConfigDict(extra="forbid")

    plan_item: str = Field(..., min_length=1)
    disposition: Literal["completed", "adapted", "not_applicable", "blocked"]
    proof: str = Field(..., min_length=1)
    rationale: str | None = None
    verdict_id: str | None = None
    capture_handles: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _validate_ui_evidence(self) -> PlanItemProof:
        if self.disposition != "completed" and not (self.rationale or "").strip():
            raise ValueError(f"{self.disposition} plan items require a non-empty rationale")
        if self.disposition in {"not_applicable", "blocked"}:
            return self
        if not UI_LABEL_RE.search(self.plan_item):
            return self
        if not self.verdict_id:
            raise ValueError(
                "UI plan items require verdict_id (criterion 11: design "
                "evidence must cite a criterion 8 verdict)"
            )
        if not self.capture_handles:
            raise ValueError(
                "UI plan items require capture_handles (criterion 11: "
                "design evidence must cite server-minted capture handles)"
            )
        media_re = re.compile(r"^ralph://media/[^/\s]+$")
        for handle in self.capture_handles:
            if not media_re.fullmatch(handle):
                raise ValueError(
                    f"capture_handle {handle!r} is not a valid "
                    "ralph://media/{artifact_id} URI; criterion 11 requires "
                    "server-minted handles, never agent-fabricated paths"
                )
        return self


def is_ui_plan_item(plan_item: str) -> bool:
    """Return True iff the plan_item text matches :data:`UI_LABEL_RE`."""
    return bool(UI_LABEL_RE.search(plan_item))


def validate(value: PlanItemProof | Mapping[str, object]) -> list[str]:
    """Validate a plan-item proof; return a list of error messages."""
    if isinstance(value, PlanItemProof):
        try:
            PlanItemProof.model_validate(value.model_dump())
            return []
        except Exception as exc:
            return [str(exc)]
    try:
        PlanItemProof.model_validate(value)
        return []
    except Exception as exc:
        return [str(exc)]
