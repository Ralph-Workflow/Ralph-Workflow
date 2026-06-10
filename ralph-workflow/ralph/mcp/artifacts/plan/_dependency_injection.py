"""Dependency-injection sub-section for the plan design schema."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel

PreferredPattern = Literal[
    "constructor",
    "parameter",
    "interface",
    "service-locator",
    "ambient-context",
    "unknown",
]

ForbiddenPattern = Literal[
    "global-singleton",
    "module-level-mutable-state",
    "import-time-side-effects",
    "subprocess-time-random",
    "env-var-direct-read",
    "unknown",
]


class DependencyInjection(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    required_for_testability: bool
    preferred_patterns: list[PreferredPattern] = Field(default_factory=list)
    forbidden_patterns: list[ForbiddenPattern] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=2000)


__all__ = [
    "DependencyInjection",
    "ForbiddenPattern",
    "PreferredPattern",
]
