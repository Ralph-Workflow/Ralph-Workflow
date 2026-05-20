"""Authoring-time policy block models."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel
from ralph.policy.models._phase_definition import PhaseDefinition


class GroupPolicyBlock(_FrozenPolicyModel):
    """Composite authoring block that owns lifecycle semantics."""

    kind: Literal["group"] = "group"
    child_blocks: list[str] = Field(
        default_factory=list,
        description="Nested blocks in authored execution order.",
    )
    completion_block: str = Field(
        ...,
        description="Individual block whose success marks lifecycle completion.",
    )
    before_complete: list[str] = Field(
        default_factory=list,
        description="Child blocks treated as pre-completion hooks.",
    )
    after_complete: list[str] = Field(
        default_factory=list,
        description="Child blocks treated as post-completion hooks.",
    )
    increments_counter: str | None = Field(
        default=None,
        description="Budget counter incremented when this lifecycle completes.",
    )
    loop_resets: list[str] = Field(
        default_factory=list,
        description="Loop counters reset when this lifecycle completes.",
    )


class IndividualPolicyBlock(_FrozenPolicyModel):
    """Leaf authoring block that compiles directly to one runtime phase."""

    kind: Literal["individual"] = "individual"
    phase_name: str = Field(..., description="Compiled runtime phase name for this block.")
    phase: PhaseDefinition = Field(..., description="Compiled runtime phase definition.")


PolicyBlock: TypeAlias = Annotated[
    GroupPolicyBlock | IndividualPolicyBlock,
    Field(discriminator="kind"),
]
