"""Summary section for the plan artifact schema.

Carries the explicit ``intent`` and ``intent_verb`` analysis fields in
addition to the existing ``context`` and ``scope_items`` fields. ``intent`` is
a free-form 1-line user-facing outcome (defaults to empty string so it is
dropped by ``model_dump(exclude_defaults=True)``, mirroring ``context``).
``intent_verb`` and ``coverage_areas`` are descriptive planner hints with no
runtime consumer. They accept project-specific vocabulary, while validators
still normalize whitespace and reject malformed non-string values.
"""

from __future__ import annotations

from pydantic import ConfigDict, Field, field_validator

from ralph.mcp.artifacts.plan._scope_item import ScopeItem
from ralph.pydantic_compat import RalphBaseModel

type CoverageArea = str


class Summary(RalphBaseModel):
    """Summary section of a plan artifact.

    Captures the user-facing context, descriptive intent hints, scope items,
    and coverage areas. None of these vocabulary choices controls execution.
    """

    model_config = ConfigDict(extra="forbid")

    context: str = Field(
        default="",
        max_length=8000,
        description="Free-form context (max 8000 chars; medium tier).",
    )
    intent: str = Field(
        default="",
        max_length=500,
        description="One-line user-facing outcome (max 500 chars; short tier).",
    )
    intent_verb: str = Field(
        default="",
        max_length=200,
        description="Free-form normalized planning-intent hint.",
    )
    scope_items: list[ScopeItem] = Field(
        default_factory=list,
        max_length=200,
        description=(
            "Scope items bounding the work (max 200); see ScopeItem. "
            "Optional — a plan may carry scope in free prose instead."
        ),
    )
    coverage_areas: list[CoverageArea] = Field(
        default_factory=list,
        max_length=50,
        description="Optional free-form coverage hints (max 50).",
    )

    @field_validator("intent")
    @classmethod
    def _strip_intent(cls, value: str) -> str:
        return value.strip()

    @field_validator("intent_verb", mode="before")
    @classmethod
    def _normalize_intent_verb(cls, value: object) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            msg = "intent_verb must be a string"
            raise ValueError(msg)
        stripped = value.strip()
        if not stripped:
            msg = "intent_verb must not be empty"
            raise ValueError(msg)
        return stripped.lower()

    @field_validator("coverage_areas", mode="before")
    @classmethod
    def _validate_coverage_areas(cls, value: object) -> list[CoverageArea]:
        if value is None:
            return []
        if not isinstance(value, list):
            msg = "coverage_areas must be a list"
            raise ValueError(msg)
        cleaned: list[CoverageArea] = []
        for entry in value:
            if not isinstance(entry, str):
                msg = f"coverage_areas elements must be strings, got {type(entry).__name__}"
                raise ValueError(msg)
            stripped = entry.strip()
            if stripped:
                cleaned.append(stripped)
        return cleaned


__all__ = ["CoverageArea", "Summary"]
