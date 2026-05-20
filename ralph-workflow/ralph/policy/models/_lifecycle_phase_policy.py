"""Compiled lifecycle-completion metadata for block-authored policies."""

from __future__ import annotations

from pydantic import Field

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class LifecyclePhasePolicy(_FrozenPolicyModel):
    """Lifecycle-owned accounting metadata keyed by compiled completion phase."""

    lifecycle_name: str = Field(..., description="Authored group-block name")
    completion_block: str = Field(..., description="Authored individual block that completes lifecycle")
    increments_counter: str | None = Field(
        default=None,
        description="Budget counter incremented when this lifecycle completes.",
    )
    loop_resets: list[str] = Field(
        default_factory=list,
        description="Loop counters reset at lifecycle completion.",
    )
    before_complete: list[str] = Field(
        default_factory=list,
        description="Authored blocks treated as pre-completion hooks.",
    )
    after_complete: list[str] = Field(
        default_factory=list,
        description="Authored blocks treated as post-completion hooks.",
    )
