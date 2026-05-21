"""Authoring-time individual policy block model."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Literal, cast

from pydantic import Field

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel

if TYPE_CHECKING:
    from ralph.policy.models._phase_definition import PhaseDefinition
else:
    PhaseDefinition = cast(
        "type[object]",
        import_module("ralph.policy.models._phase_definition").PhaseDefinition,
    )


class IndividualPolicyBlock(_FrozenPolicyModel):
    """Leaf authoring block that compiles directly to one runtime phase."""

    kind: Literal["individual"] = "individual"
    phase_name: str = Field(..., description="Compiled runtime phase name for this block.")
    phase: PhaseDefinition = Field(..., description="Compiled runtime phase definition.")
