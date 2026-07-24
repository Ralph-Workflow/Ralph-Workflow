"""Refactor strategy sub-section for the plan design schema."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel

type RefactorApproach = str
type DeadCodePolicy = str


class RefactorStrategy(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    approach: RefactorApproach = Field(
        ...,
        min_length=1,
        description="Free-form refactor approach.",
    )
    preserve_public_api: bool | None = Field(
        default=None,
        description="Whether the public API must be preserved.",
    )
    dead_code_policy: DeadCodePolicy = Field(
        default="delete-immediately",
        min_length=1,
        description="Free-form dead-code policy.",
    )
    allow_temporary_hacks: bool = Field(
        default=False,
        description="Whether temporary hacks are allowed during the refactor.",
    )


__all__ = [
    "DeadCodePolicy",
    "RefactorApproach",
    "RefactorStrategy",
]
