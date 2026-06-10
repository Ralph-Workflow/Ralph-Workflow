"""Refactor strategy sub-section for the plan design schema."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict

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

    approach: RefactorApproach
    preserve_public_api: bool | None = None
    dead_code_policy: DeadCodePolicy = "delete-immediately"
    allow_temporary_hacks: bool = False


__all__ = [
    "DeadCodePolicy",
    "RefactorApproach",
    "RefactorStrategy",
]
