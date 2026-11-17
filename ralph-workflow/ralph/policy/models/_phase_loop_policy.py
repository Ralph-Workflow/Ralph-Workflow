from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class PhaseLoopPolicy(RalphBaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    iteration_state_field: str = Field(...)
    loopback_review_outcome: str | None = None
