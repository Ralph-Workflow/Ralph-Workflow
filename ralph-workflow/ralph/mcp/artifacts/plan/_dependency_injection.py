"""Dependency-injection sub-section for the plan design schema."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel

type PreferredPattern = str
type ForbiddenPattern = str


class DependencyInjection(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    required_for_testability: bool = Field(
        ...,
        description="Whether DI is required for testability.",
    )
    preferred_patterns: list[PreferredPattern] = Field(
        default_factory=list,
        max_length=20,
        description="Free-form preferred-pattern hints (max 20).",
    )
    forbidden_patterns: list[ForbiddenPattern] = Field(
        default_factory=list,
        max_length=50,
        description="Free-form forbidden-pattern hints (max 50).",
    )
    notes: str | None = Field(
        default=None,
        max_length=8000,
        description="Optional free-form notes (max 8000 chars; medium tier).",
    )


__all__ = [
    "DependencyInjection",
    "ForbiddenPattern",
    "PreferredPattern",
]
