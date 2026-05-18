"""Immutable view-model for phase-start banner data."""

from __future__ import annotations

from dataclasses import dataclass

from ralph.display.phase_status import (
    PhaseIterationContext,
    format_analysis_cycle,
    format_dev_cycle,
)


@dataclass(frozen=True)
class PhaseEntryModel:
    """Immutable view-model for phase-start banner data."""

    phase_name: str
    phase_role: str | None = None
    agent_name: str | None = None
    outer_dev_iteration: int | None = None
    outer_dev_cap: int | None = None
    inner_analysis: int | None = None
    inner_analysis_cap: int | None = None

    def to_iteration_context(self) -> PhaseIterationContext:
        """Return a PhaseIterationContext for canonical label rendering."""
        return PhaseIterationContext(
            outer_dev=self.outer_dev_iteration,
            outer_dev_cap=self.outer_dev_cap,
            inner_analysis=self.inner_analysis,
            inner_analysis_cap=self.inner_analysis_cap,
        )

    def human_label(self) -> str:
        """Return the human-readable phase label."""
        return self.phase_name.replace("_", " ").title()

    def iteration_label_parts(self) -> list[str]:
        """Return ordered canonical label strings for the iteration context."""
        parts: list[str] = []
        if self.outer_dev_iteration is not None:
            parts.append(format_dev_cycle(self.outer_dev_iteration))
        if self.inner_analysis is not None:
            parts.append(format_analysis_cycle(self.inner_analysis, self.inner_analysis_cap))
        return parts
