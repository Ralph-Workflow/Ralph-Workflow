from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PlanSummary:
    summary: str | None = None
    scope_items: tuple[str, ...] = ()
    total_steps: int = 0
    risks_mitigations: tuple[str, ...] = field(default_factory=tuple)
