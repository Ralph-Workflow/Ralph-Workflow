"""Event and status emission helpers for the conflict resolution driver."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

    MonotonicClock = Callable[[], float]


def _emit(display: ParallelDisplay | None, line: str) -> None:
    from ralph.pipeline.conflict_resolution import driver as driver_mod

    driver_mod.emit_conflict_phase_line(display, line)


def announce_declared_decisions(
    display: ParallelDisplay | None, decision_paths: tuple[str, ...]
) -> None:
    """Name the paths whose resolution Ralph cannot read off the file."""
    if not decision_paths:
        return
    _emit(
        display,
        "conflict(s) git could not write markers into, so the resolution cannot be "
        f"read off the file and must be declared: {', '.join(decision_paths)}",
    )


def emit_expired_operator_cap(
    display: ParallelDisplay | None,
    session: ResolutionSession,
    conflicted: tuple[str, ...],
    clock: MonotonicClock,
) -> None:
    """Report a rebase-wide cap before another resolver process is launched."""
    session.terminal_reason = ResolutionTerminationReason.OPERATOR_CAP_REACHED
    session.last_activity_kind = None
    session.last_activity_at = None
    session.last_duration_seconds = max(0.0, clock() - (session.started_at or clock()))
    emit_attempt_outcome(
        display,
        ResolutionOutcome(
            succeeded=False,
            reason=session.terminal_reason,
            duration_seconds=session.last_duration_seconds,
            last_activity_kind=None,
            last_activity_at=None,
            unresolved_paths=session.unresolved_paths or conflicted,
        ),
    )


def emit_success(
    display: ParallelDisplay | None, round_index: int, stop: RebaseStop | None
) -> None:
    next_action = (
        "verifying and continuing the rebase"
        if stop is not None
        else "verifying and committing the merge"
    )
    _emit(display, f"conflicts resolved in round {round_index}; {next_action}")


def emit_attempt_outcome(display: ParallelDisplay | None, outcome: ResolutionOutcome) -> None:
    """Render a typed non-success outcome without misclassifying it as a hang."""
    if outcome.succeeded or outcome.reason is None:
        return
    _emit(
        display,
        f"{outcome.reason.value}: duration={outcome.duration_seconds:.1f}s; "
        f"last_activity_kind={outcome.last_activity_kind or 'none'}; "
        f"last_activity_at={outcome.last_activity_at or 'never'}; "
        f"last_progress=unresolved_count={len(outcome.unresolved_paths)}; "
        f"unresolved_paths={', '.join(outcome.unresolved_paths)}; "
        f"next=inspect typed reason, keep landed rebase stops, and retry only if identity changed",
    )


__all__ = [
    "announce_declared_decisions",
    "emit_attempt_outcome",
    "emit_expired_operator_cap",
    "emit_success",
]
