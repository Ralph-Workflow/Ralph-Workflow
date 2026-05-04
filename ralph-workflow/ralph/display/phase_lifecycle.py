"""Lifecycle view-model dataclasses for phase rendering.

This is the single source of truth for the data and field ordering used by
phase-start banners, phase-exit recaps, and final run summaries.  All three
surfaces (``show_phase_start``, ``emit_phase_close``, ``render_completion_summary``)
must express iteration context, budget, and performance in a consistent
vocabulary drawn from this module.

All dataclasses are pure:

- No ``Console(...)`` construction.
- No environment reads.
- No Rich rendering.

Canonical wording helpers from :mod:`ralph.display.phase_status` (e.g.
``format_dev_cycle``, ``format_analysis_cycle``) are reused so label strings
never diverge between phase-start banners and phase-close after-banners.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.display.phase_status import (
    PhaseIterationContext,
    format_analysis_cycle,
    format_budget_remaining,
    format_dev_cycle,
)

if TYPE_CHECKING:
    from ralph.display.snapshot import PipelineSnapshot


@dataclass(frozen=True)
class PhaseEntryModel:
    """Immutable view-model for phase-start banner data.

    Carries all the data needed to render a phase-start banner in a stable,
    unambiguous hierarchy.  Field ordering matches the canonical display order:
    phase identity → outer dev → inner analysis → budget → fixer → agent.

    Attributes:
        phase_name: Raw phase name (e.g. ``"development_analysis"``).
        phase_role: Role derived from pipeline policy (e.g. ``"analysis"``).
        agent_name: Active agent identity string, if known.
        outer_dev_iteration: Outer development cycle number (1-indexed).
        outer_dev_cap: Budget cap for the outer development counter.
        inner_analysis: Inner analysis cycle number within the current context.
        inner_analysis_cap: Cap for inner analysis (shown as ``N/cap``).
        budget_remaining: Remaining budget count for the active budget counter.
        budget_counter_name: Name of the budget counter driving ``budget_remaining``.
    """

    phase_name: str
    phase_role: str | None = None
    agent_name: str | None = None
    outer_dev_iteration: int | None = None
    outer_dev_cap: int | None = None
    inner_analysis: int | None = None
    inner_analysis_cap: int | None = None
    budget_remaining: int | None = None
    budget_counter_name: str | None = None

    def to_iteration_context(self) -> PhaseIterationContext:
        """Return a :class:`PhaseIterationContext` for canonical label rendering."""
        return PhaseIterationContext(
            outer_dev=self.outer_dev_iteration,
            outer_dev_cap=self.outer_dev_cap,
            inner_analysis=self.inner_analysis,
            inner_analysis_cap=self.inner_analysis_cap,
            budget_remaining=self.budget_remaining,
        )

    def human_label(self) -> str:
        """Return the human-readable phase label (e.g. ``"Development Analysis"``)."""
        return self.phase_name.replace("_", " ").title()

    def iteration_label_parts(self) -> list[str]:
        """Return ordered canonical label strings for the iteration context.

        Used by renderers that need to surface iteration state in a
        consistent, space-efficient form.
        """
        parts: list[str] = []
        if self.outer_dev_iteration is not None:
            parts.append(format_dev_cycle(self.outer_dev_iteration))
        if self.inner_analysis is not None:
            parts.append(format_analysis_cycle(self.inner_analysis, self.inner_analysis_cap))
        if self.budget_remaining is not None:
            parts.append(format_budget_remaining(self.budget_remaining))
        return parts


@dataclass(frozen=True)
class PhaseExitModel:
    """Immutable view-model for phase-close after-banner data.

    Extends :class:`PhaseEntryModel` with phase-level performance statistics
    and exit context so the ``[phase-close]`` line can act as a real
    after-banner rather than a terse metrics suffix.

    Attributes:
        phase_name: Raw phase name.
        phase_role: Role from pipeline policy.
        agent_name: Active agent identity, if known.
        outer_dev_iteration: Outer development cycle number.
        outer_dev_cap: Budget cap for the outer development counter.
        inner_analysis: Inner analysis cycle number.
        inner_analysis_cap: Cap for inner analysis.
        budget_remaining: Remaining budget count.
        budget_counter_name: Budget counter name.
        elapsed_seconds: Wall-clock time for this phase.
        exit_trigger: Why the phase ended (e.g. ``"produced"``, ``"timeout"``).
        content_blocks: Number of content streaming blocks in this phase.
        thinking_blocks: Number of thinking streaming blocks.
        tool_calls: Number of tool-use events.
        errors: Number of error events.
        artifact_outcome: Human-readable description of the produced artifact
            (e.g. ``"plan: 5 step(s), 2 risk(s)"``).
        review_issues_found: Whether the phase found review issues, or ``None``
            when not applicable.
        waiting_status_line: Last recorded waiting-status line for debug breadcrumbs.
        last_failure_category: Last recorded failure category for debug breadcrumbs.
    """

    phase_name: str
    phase_role: str | None = None
    agent_name: str | None = None
    outer_dev_iteration: int | None = None
    outer_dev_cap: int | None = None
    inner_analysis: int | None = None
    inner_analysis_cap: int | None = None
    budget_remaining: int | None = None
    budget_counter_name: str | None = None
    # Performance / activity
    elapsed_seconds: float = 0.0
    exit_trigger: str | None = None
    content_blocks: int = 0
    thinking_blocks: int = 0
    tool_calls: int = 0
    errors: int = 0
    # Outcome
    artifact_outcome: str = ""
    review_issues_found: bool | None = None
    # Debug breadcrumbs
    waiting_status_line: str | None = None
    last_failure_category: str | None = None

    def to_iteration_context(self) -> PhaseIterationContext:
        """Return a :class:`PhaseIterationContext` for canonical label rendering."""
        return PhaseIterationContext(
            outer_dev=self.outer_dev_iteration,
            outer_dev_cap=self.outer_dev_cap,
            inner_analysis=self.inner_analysis,
            inner_analysis_cap=self.inner_analysis_cap,
            budget_remaining=self.budget_remaining,
        )

    @classmethod
    def from_entry_model(  # noqa: PLR0913
        cls,
        entry: PhaseEntryModel,
        *,
        elapsed_seconds: float = 0.0,
        exit_trigger: str | None = None,
        content_blocks: int = 0,
        thinking_blocks: int = 0,
        tool_calls: int = 0,
        errors: int = 0,
        artifact_outcome: str = "",
        review_issues_found: bool | None = None,
        waiting_status_line: str | None = None,
        last_failure_category: str | None = None,
    ) -> PhaseExitModel:
        """Construct a :class:`PhaseExitModel` by extending a :class:`PhaseEntryModel`."""
        return cls(
            phase_name=entry.phase_name,
            phase_role=entry.phase_role,
            agent_name=entry.agent_name,
            outer_dev_iteration=entry.outer_dev_iteration,
            outer_dev_cap=entry.outer_dev_cap,
            inner_analysis=entry.inner_analysis,
            inner_analysis_cap=entry.inner_analysis_cap,
            budget_remaining=entry.budget_remaining,
            budget_counter_name=entry.budget_counter_name,
            elapsed_seconds=elapsed_seconds,
            exit_trigger=exit_trigger,
            content_blocks=content_blocks,
            thinking_blocks=thinking_blocks,
            tool_calls=tool_calls,
            errors=errors,
            artifact_outcome=artifact_outcome,
            review_issues_found=review_issues_found,
            waiting_status_line=waiting_status_line,
            last_failure_category=last_failure_category,
        )


@dataclass(frozen=True)
class RunCompletionModel:
    """Immutable view-model for final run-completion summary data.

    Aggregates all fields needed by the completion panel and ``[run-end]``
    transcript block so both surfaces present a consistent picture.

    Attributes:
        final_phase: The pipeline phase at termination.
        is_failure: ``True`` when the pipeline terminated with a failure.
        exit_trigger: Canonical exit-trigger label (e.g. ``"completed"``,
            ``"failed"``, ``"interrupted"``).
        elapsed_seconds: Total wall-clock time for the run.
        outer_dev_iteration: Outer development cycle at termination.
        total_agent_calls: Total agent invocations across the run.
        content_blocks: Total content streaming blocks.
        thinking_blocks: Total thinking streaming blocks.
        tool_calls: Total tool-use events.
        errors: Total error events.
        review_issues_found: Whether the review phase found issues.
        last_error: Last recorded error message, if any.
        budget_progress: Mapping of counter name to ``(completed, cap)`` tuples
            for budget-tracked counters.
    """

    final_phase: str
    is_failure: bool
    exit_trigger: str = "exited"
    elapsed_seconds: float | None = None
    # Iteration context
    outer_dev_iteration: int | None = None
    # Activity counters
    total_agent_calls: int = 0
    content_blocks: int = 0
    thinking_blocks: int = 0
    tool_calls: int = 0
    errors: int = 0
    # Review
    review_issues_found: bool = False
    # Error diagnostics
    last_error: str | None = None
    # Budget progress: counter_name → (completed, cap)
    budget_progress: dict[str, tuple[int, int]] = field(default_factory=dict)
    # Analysis decision trace: (phase, decision, reason) for analysis phases
    analysis_decisions: tuple[tuple[str, str, str], ...] = ()
    # Debug breadcrumbs: last activity, waiting state, and failure category
    last_activity_line: str | None = None
    waiting_status_line: str | None = None
    last_failure_category: str | None = None

    @classmethod
    def from_snapshot(  # noqa: PLR0913
        cls,
        snapshot: PipelineSnapshot,
        *,
        exit_trigger: str,
        elapsed_seconds: float | None = None,
        content_blocks: int = 0,
        thinking_blocks: int = 0,
        tool_calls: int = 0,
        errors: int = 0,
    ) -> RunCompletionModel:
        """Build a :class:`RunCompletionModel` from a :class:`PipelineSnapshot`."""
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
            content_blocks=content_blocks,
            thinking_blocks=thinking_blocks,
            tool_calls=tool_calls,
            errors=errors,
            review_issues_found=snapshot.review_issues_found,
            last_error=snapshot.last_error,
            budget_progress=budget_progress,
            analysis_decisions=analysis_decisions,
            last_activity_line=snapshot.last_activity_line,
            waiting_status_line=snapshot.waiting_status_line,
            last_failure_category=snapshot.last_failure_category,
        )


__all__ = [
    "PhaseEntryModel",
    "PhaseExitModel",
    "RunCompletionModel",
]
