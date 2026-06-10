"""Design constraints sub-section for the plan design schema."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, field_validator

from ralph.pydantic_compat import RalphBaseModel

ArchitectureStyle = Literal[
    "monolith",
    "modular-monolith",
    "microservice",
    "library",
    "cli",
    "spa",
    "mobile",
    "serverless",
    "embedded",
    "unknown",
]

_MAX_ENTRY_LENGTH = 500


class DesignConstraints(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, max_length=2000)
    invariants: list[str] = Field(default_factory=list)
    architecture_style: ArchitectureStyle | None = None

    @field_validator("text")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "design constraints text must not be empty"
            raise ValueError(msg)
        return stripped

    @field_validator("invariants")
    @classmethod
    def _clean_invariants(cls, invariants: list[str]) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for entry in invariants:
            stripped = entry.strip()
            if not stripped or len(stripped) > _MAX_ENTRY_LENGTH:
                continue
            lowered = stripped.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(stripped)
        return deduped


__all__ = ["ArchitectureStyle", "DesignConstraints"]
