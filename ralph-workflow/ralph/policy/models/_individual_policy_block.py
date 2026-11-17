from __future__ import annotations

from typing import Literal

from pydantic import Field

from ralph.policy.models._phase_definition import PhaseDefinition
from ralph.pydantic_compat import RalphBaseModel


class IndividualPolicyBlock(RalphBaseModel):
    kind: Literal["individual"] = "individual"
    phase_name: str = Field(..., description="Compiled runtime phase name for this block.")
    phase: PhaseDefinition = Field(..., description="Compiled runtime phase definition.")
