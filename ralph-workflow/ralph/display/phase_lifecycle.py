"""Lifecycle view-model dataclasses for phase rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.display.exit_context import ExitContext
from ralph.display.phase_activity_counts import PhaseActivityCounts
from ralph.display.phase_entry_model import PhaseEntryModel
from ralph.display.phase_exit_model import PhaseExitModel

if TYPE_CHECKING:
    from ralph.display.snapshot import PipelineSnapshot


@dataclass(frozen=True)
class RunCompletionModel:
    """Immutable view-model for final run-completion summary data."""

    final_phase: str
    is_failure: bool
    exit_trigger: str = "exited"
    elapsed_seconds: float | None = None
    outer_dev_iteration: int | None = None
    total_agent_calls: int = 0
    content_blocks: int = 0
    thinking_blocks: int = 0
    tool_calls: int = 0
    errors: int = 0
    review_issues_found: bool = False
    last_error: str | None = None
    budget_progress: dict[str, tuple[int, int]] = field(default_factory=dict)
    analysis_decisions: tuple[tuple[str, str, str], ...] = ()
    last_activity_line: str | None = None
    waiting_status_line: str | None = None
    last_failure_category: str | None = None
    mcp_restart_count: int = 0

    @classmethod
    def from_snapshot(
        cls,
        snapshot: PipelineSnapshot,
        *,
        exit_trigger: str,
        elapsed_seconds: float | None = None,
        activity: PhaseActivityCounts | None = None,
    ) -> RunCompletionModel:
        """Build a RunCompletionModel from a PipelineSnapshot."""
        effective_activity = activity or PhaseActivityCounts()
        budget_progress: dict[str, tuple[int, int]] = {
            name: (bp.completed, bp.cap)
            for name, bp in snapshot.budget_progress.items()
            if bp.tracks_budget and bp.cap > 0
        }
        analysis_decisions: tuple[tuple[str, str, str], ...] = tuple(
            (phase, decision, reason)
            for phase, decision, reason, _ts in snapshot.decision_log
            if "analysis" in phase.lower()
        )
        return cls(
            final_phase=snapshot.phase,
            is_failure=snapshot.is_terminal_failure,
            exit_trigger=exit_trigger,
            elapsed_seconds=elapsed_seconds,
            outer_dev_iteration=snapshot.outer_dev_iteration,
            total_agent_calls=snapshot.total_agent_calls,
            content_blocks=effective_activity.content_blocks,
            thinking_blocks=effective_activity.thinking_blocks,
            tool_calls=effective_activity.tool_calls,
            errors=effective_activity.errors,
            review_issues_found=snapshot.review_issues_found,
            last_error=snapshot.last_error,
            budget_progress=budget_progress,
            analysis_decisions=analysis_decisions,
            last_activity_line=snapshot.last_activity_line,
            waiting_status_line=snapshot.waiting_status_line,
            last_failure_category=snapshot.last_failure_category,
            mcp_restart_count=snapshot.mcp_restart_count,
        )


__all__ = [
    "ExitContext",
    "PhaseActivityCounts",
    "PhaseEntryModel",
    "PhaseExitModel",
    "RunCompletionModel",
]
