"""Event and status emission helpers for the conflict resolution driver."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
from ralph.pipeline.conflict_resolution.status import emit_conflict_phase_line

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

    MonotonicClock = Callable[[], float]


def _emit(display: ParallelDisplay | None, line: str) -> None:
    from ralph.pipeline.conflict_resolution import driver as driver_mod

    fn = cast(
        "Callable[[ParallelDisplay | None, str], None] | None",
        getattr(driver_mod, "emit_conflict_phase_line", None),
    )
    if fn is not None:
        fn(display, line)
        return
    emit_conflict_phase_line(display, line)


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
    session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
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
            failure_cause=ResolutionFailureCause.AGENT_FAILURE,
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


def render_round_prompt(
    root: Path,
    target: str,
    conflicted: Sequence[str],
    round_index: int,
    round_cap: int,
    session: ResolutionSession,
    stop: RebaseStop | None,
    strategy_history: tuple[str, ...],
) -> Path | None:
    """Render one round without breaking legacy spy seams on empty history."""
    from ralph.pipeline.conflict_resolution import driver as driver_mod

    render_fn = driver_mod.render_conflict_prompt
    sha = stop.sha if stop is not None else None
    subj = stop.subject if stop is not None else None
    idx = stop.stop_index if stop is not None else None
    cap = stop.stop_cap if stop is not None else None
    surviving = () if round_index == 1 else session.unresolved_paths
    if strategy_history:
        return render_fn(
            root=root,
            target=target,
            conflicted_paths=conflicted,
            round_index=round_index,
            round_cap=round_cap,
            surviving_marker_paths=surviving,
            replaying_commit_sha=sha,
            replaying_commit_subject=subj,
            stop_index=idx,
            stop_cap=cap,
            strategy_history=strategy_history,
        )
    return render_fn(
        root=root,
        target=target,
        conflicted_paths=conflicted,
        round_index=round_index,
        round_cap=round_cap,
        surviving_marker_paths=surviving,
        replaying_commit_sha=sha,
        replaying_commit_subject=subj,
        stop_index=idx,
        stop_cap=cap,
    )


__all__ = [
    "announce_declared_decisions",
    "emit_attempt_outcome",
    "emit_expired_operator_cap",
    "emit_success",
    "render_round_prompt",
]
