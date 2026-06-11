"""Project-level cross-cutting constraints for the plan artifact.

The ``PlanConstraints`` model captures do-not-break rules the executor
must respect independently of any single design decision. Unlike the
``design.constraints`` sub-section (which is bias-filled from the
``planning_profile`` preset and lives under the optional ``design``
field), the top-level ``PlanConstraints`` is a separate section that
appears in the agent-facing markdown as a `## Project Constraints`
heading and is exposed in the step-wise submit section tool.

The four fields are mutually non-overlapping:

- ``must_not_break`` and ``must_keep_working`` are list-of-strings
  invariants (each 1-200 chars, deduped case-insensitively)
- ``performance_budget`` and ``security_posture`` are single-line
  free-form fields (each 1-200 chars)

The ``_clean_entries`` validator strips whitespace, drops empties,
dedupes by lower-case, and enforces a 50-entry cap per list.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import ConfigDict, Field, StringConstraints, field_validator

from ralph.pydantic_compat import RalphBaseModel

_MAX_CONSTRAINT_ENTRY_LENGTH = 200
_MAX_CONSTRAINT_LIST_ENTRIES = 50

ConstraintEntry = Annotated[
    str,
    StringConstraints(min_length=1, max_length=_MAX_CONSTRAINT_ENTRY_LENGTH),
]


class PlanConstraints(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    must_not_break: list[ConstraintEntry] = Field(default_factory=list)
    must_keep_working: list[ConstraintEntry] = Field(default_factory=list)
    performance_budget: str | None = Field(default=None, max_length=_MAX_CONSTRAINT_ENTRY_LENGTH)
    security_posture: str | None = Field(default=None, max_length=_MAX_CONSTRAINT_ENTRY_LENGTH)

    @field_validator("must_not_break", "must_keep_working", mode="before")
    @classmethod
    def _clean_entries(cls, value: object) -> object:
        """Strip whitespace, drop empties, dedupe case-insensitively.

        Cap the list at ``_MAX_CONSTRAINT_LIST_ENTRIES=50`` entries so
        a runaway plan cannot bloat the constraints list. The
        before-mode validator accepts the raw list so empty strings can
        be silently dropped; the underlying ``ConstraintEntry`` type
        still rejects the empty string at the per-entry type level
        (the field-validator runs after this before-validator).
        """
        if not isinstance(value, list):
            return value
        cleaned: list[str] = []
        position_by_lowered: dict[str, int] = {}
        for entry in value:
            if not isinstance(entry, str):
                return value
            stripped = entry.strip()
            if not stripped:
                continue
            lowered = stripped.lower()
            if lowered in position_by_lowered:
                continue
            position_by_lowered[lowered] = len(cleaned)
            cleaned.append(stripped)
        if len(cleaned) > _MAX_CONSTRAINT_LIST_ENTRIES:
            msg = f"constraint list has more than {_MAX_CONSTRAINT_LIST_ENTRIES} entries"
            raise ValueError(msg)
        return cleaned


__all__ = ["PlanConstraints"]
