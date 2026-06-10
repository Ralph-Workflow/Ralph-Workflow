"""Non-goals sub-section for the plan design schema."""

from __future__ import annotations

from pydantic import ConfigDict, Field, field_validator

from ralph.pydantic_compat import RalphBaseModel

_MAX_ENTRY_LENGTH = 500


class NonGoals(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[str] = Field(..., min_length=1)

    @field_validator("items")
    @classmethod
    def _clean_items(cls, items: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for entry in items:
            stripped = entry.strip()
            if not stripped or len(stripped) > _MAX_ENTRY_LENGTH:
                continue
            lowered = stripped.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(stripped)
        if not deduped:
            msg = "non_goals.items must contain at least one non-empty entry"
            raise ValueError(msg)
        return deduped


__all__ = ["NonGoals"]
