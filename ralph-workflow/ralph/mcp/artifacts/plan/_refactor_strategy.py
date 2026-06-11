"""Refactor strategy sub-section for the plan design schema."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel

RefactorApproach = Literal[
    "greenfield",
    "incremental",
    "strangler",
    "branch-by-abstraction",
    "rebuild-in-parallel",
    "no-refactor",
]

DeadCodePolicy = Literal[
    "delete-immediately",
    "delete-after-feature",
    "keep-for-trace",
    "unknown",
]


class RefactorStrategy(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    approach: RefactorApproach = Field(
        ...,
        description="RefactorApproach enum; see RefactorApproach literal.",
    )
    preserve_public_api: bool | None = Field(
        default=None,
        description="Whether the public API must be preserved.",
    )
    dead_code_policy: DeadCodePolicy = Field(
        default="delete-immediately",
        description="DeadCodePolicy enum; see DeadCodePolicy literal.",
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
