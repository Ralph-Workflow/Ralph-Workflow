"""Summary section for the plan artifact schema.

Carries the cheap-model-friendly ``intent`` and ``intent_verb`` shortcuts in
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


class Summary(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    context: str = Field(default="", max_length=2000)
    intent: str = Field(default="", max_length=200)
    intent_verb: str = Field(default="")
    scope_items: list[ScopeItem] = Field(..., min_length=3)

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


__all__ = ["_INTENT_VERB_SET", "Summary"]
