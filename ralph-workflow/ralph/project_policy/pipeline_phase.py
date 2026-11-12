from __future__ import annotations

from dataclasses import dataclass, field

from ralph.project_policy.pipeline_route import PolicyRoute


@dataclass(frozen=True, slots=True)
class PolicyPhaseDef:
    drain: str
    role: str
    on_success: str | None = None
    decisions: dict[str, PolicyRoute] = field(default_factory=dict[str, PolicyRoute])


__all__ = ["PolicyPhaseDef"]
