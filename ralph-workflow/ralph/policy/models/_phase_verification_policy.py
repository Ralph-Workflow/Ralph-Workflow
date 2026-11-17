from __future__ import annotations

from typing import Literal

from ralph.pydantic_compat import RalphBaseModel


class PhaseVerificationPolicy(RalphBaseModel):
    kind: Literal["artifact", "none"]
    gate_for: Literal["advancement", "completion", "release"]
    on_failure_route: str | None = None
