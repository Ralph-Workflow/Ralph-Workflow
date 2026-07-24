"""Plan artifact summary projection for display."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PlanSummary:
    """A stable, presentation-friendly projection of a plan Markdown artifact."""

    summary: str | None = None
    scope_items: tuple[str, ...] = ()
    total_steps: int = 0
    risks_mitigations: tuple[str, ...] = field(default_factory=tuple)
