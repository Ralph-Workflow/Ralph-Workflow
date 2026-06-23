"""Summary section for the plan artifact schema.

Carries the explicit ``intent`` and ``intent_verb`` analysis fields in
addition to the existing ``context`` and ``scope_items`` fields. ``intent`` is
a free-form 1-line user-facing outcome (defaults to empty string so it is
dropped by ``model_dump(exclude_defaults=True)``, mirroring ``context``).
``intent_verb`` is a closed enum stored as ``str`` defaulting to ``""`` (also
dropped from ``model_dump(exclude_defaults=True)``). A before-validator
lowercases the value before the closed-set check fires (so ``Add`` and
``ADD`` both pass), rejects unknown values, and explicitly rejects ``""`` to
distinguish a deliberate empty value from an omitted field.
"""

from __future__ import annotations

from typing import Literal, cast

from pydantic import ConfigDict, Field, field_validator

from ralph.mcp.artifacts.plan._scope_item import ScopeItem
from ralph.pydantic_compat import RalphBaseModel

_INTENT_VERB_SET: frozenset[str] = frozenset(
    {
        "add",
        "fix",
        "refactor",
        "migrate",
        "document",
        "investigate",
        "improve",
        "configure",
        "remove",
    }
)

CoverageArea = Literal[
    "bugfix",
    "feature",
    "refactor",
    "test",
    "docs",
    "infra",
    "security",
    "performance",
    "migration",
    "release",
]

_COVERAGE_AREAS: frozenset[str] = frozenset(
    {
        "bugfix",
        "feature",
        "refactor",
        "test",
        "docs",
        "infra",
        "security",
        "performance",
        "migration",
        "release",
    }
)


class Summary(RalphBaseModel):
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
        description="Closed verb; see _INTENT_VERB_SET (9 values).",
    )
    scope_items: list[ScopeItem] = Field(
        ...,
        min_length=3,
        max_length=200,
        description="At least 3 scope items (max 200); see ScopeItem.",
    )
    coverage_areas: list[CoverageArea] = Field(
        default_factory=list,
        max_length=50,
        description="Optional CoverageArea enum list (max 50); see CoverageArea literal.",
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
        lowered = stripped.lower()
        if lowered not in _INTENT_VERB_SET:
            msg = f"intent_verb {value!r} is not one of: {sorted(_INTENT_VERB_SET)!r}"
            raise ValueError(msg)
        return lowered

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
            if entry not in _COVERAGE_AREAS:
                msg = f"coverage_areas element {entry!r} is not one of: {sorted(_COVERAGE_AREAS)!r}"
                raise ValueError(msg)
            cleaned.append(cast("CoverageArea", entry))
        return cleaned


__all__ = ["_COVERAGE_AREAS", "_INTENT_VERB_SET", "CoverageArea", "Summary"]
