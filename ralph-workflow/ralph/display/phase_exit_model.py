"""Immutable view-model for phase-close after-banner data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.display.exit_context import ExitContext
from ralph.display.phase_status import PhaseIterationContext

if TYPE_CHECKING:
    from ralph.display.phase_entry_model import PhaseEntryModel


@dataclass(frozen=True)
class PhaseExitModel:
    """Immutable view-model for phase-close after-banner data."""

    phase_name: str
    phase_role: str | None = None
    agent_name: str | None = None
    outer_dev_iteration: int | None = None
    outer_dev_cap: int | None = None
    inner_analysis: int | None = None
    inner_analysis_cap: int | None = None
    elapsed_seconds: float = 0.0
    exit_trigger: str | None = None
    content_blocks: int = 0
    thinking_blocks: int = 0
    tool_calls: int = 0
    errors: int = 0
    artifact_outcome: str = ""
    review_issues_found: bool | None = None
    routing_note: str | None = None
    waiting_status_line: str | None = None
    last_failure_category: str | None = None

    def to_iteration_context(self) -> PhaseIterationContext:
        """Return a PhaseIterationContext for canonical label rendering."""
        return PhaseIterationContext(
            outer_dev=self.outer_dev_iteration,
            outer_dev_cap=self.outer_dev_cap,
            inner_analysis=self.inner_analysis,
            inner_analysis_cap=self.inner_analysis_cap,
        )

    @classmethod
    def from_entry_model(
        cls,
        entry: PhaseEntryModel,
        context: ExitContext | None = None,
    ) -> PhaseExitModel:
        """Construct a PhaseExitModel by extending a PhaseEntryModel."""
        effective_context = context or ExitContext()
        return cls(
            phase_name=entry.phase_name,
            phase_role=entry.phase_role,
            agent_name=entry.agent_name,
            outer_dev_iteration=entry.outer_dev_iteration,
            outer_dev_cap=entry.outer_dev_cap,
            inner_analysis=entry.inner_analysis,
            inner_analysis_cap=entry.inner_analysis_cap,
            elapsed_seconds=effective_context.elapsed_seconds,
            exit_trigger=effective_context.exit_trigger,
            content_blocks=effective_context.content_blocks,
            thinking_blocks=effective_context.thinking_blocks,
            tool_calls=effective_context.tool_calls,
            errors=effective_context.errors,
            artifact_outcome=effective_context.artifact_outcome,
            review_issues_found=effective_context.review_issues_found,
            routing_note=effective_context.routing_note,
            waiting_status_line=effective_context.waiting_status_line,
            last_failure_category=effective_context.last_failure_category,
        )
