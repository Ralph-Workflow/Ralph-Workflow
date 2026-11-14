from __future__ import annotations

from typing import TYPE_CHECKING, assert_never, cast

from ralph.pipeline.conflict_resolution.status import emit_conflict_phase_line
from ralph.pipeline.resolution_outcome import (
    ConfigFailureOutcome,
    NextStrategyOutcome,
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
    UnknownOutcome,
    WaitingOutcome,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline._integration_status import IntegrationStatus
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
    display: ParallelDisplay | None, status: IntegrationStatus | None
) -> None:
    if status is None or not status.path_details:
        return
    details = "; ".join(status.path_details)
    _emit(
        display,
        "conflict(s) git could not write markers into, so the resolution cannot be "
        f"read off the file and must be declared: {details}",
    )


def emit_expired_operator_cap(
    display: ParallelDisplay | None,
    session: ResolutionSession,
    conflicted: tuple[str, ...],
    clock: MonotonicClock,
) -> None:
    from ralph.git.resolution_failure_cause import ResolutionFailureCause
    from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
        ResolutionTerminationReason,
    )

    session.terminal_reason = ResolutionTerminationReason.OPERATOR_CAP_REACHED
    session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
    session.last_activity_kind = None
    session.last_activity_at = None
    session.last_duration_seconds = max(0.0, clock() - (session.started_at or clock()))
    emit_attempt_outcome(
        display,
        NextStrategyOutcome(strategy=session.terminal_reason.value),
        session=session,
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


def emit_attempt_outcome(
    display: ParallelDisplay | None,
    outcome: StopResolutionOutcome,
    session: ResolutionSession | None = None,
) -> None:
    if isinstance(outcome, ResolvedOutcome):
        return
    if session is not None and (
        session.terminal_reason is not None
        or session.last_activity_kind is not None
        or session.last_duration_seconds is not None
    ):
        from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
            ResolutionTerminationReason,
        )

        strategy_fallback = (
            outcome.strategy
            if isinstance(outcome, NextStrategyOutcome)
            else "resolution_incomplete"
        )
        reason_label = (
            session.terminal_reason.value
            if isinstance(session.terminal_reason, ResolutionTerminationReason)
            else str(session.terminal_reason or strategy_fallback)
        )
        duration = (
            session.last_duration_seconds if session.last_duration_seconds is not None else 0.0
        )
        unresolved = session.unresolved_paths
        line = (
            f"{reason_label}: duration={duration:.1f}s; "
            f"last_activity_kind={session.last_activity_kind or 'none'}; "
            f"last_activity_at={session.last_activity_at or 'never'}; "
            f"last_progress=unresolved_count={len(unresolved)}; "
            f"unresolved_paths={', '.join(unresolved)}; "
            f"next=inspect typed reason, keep landed rebase stops, and retry only if identity changed"
        )
        _emit(display, line)
        return
    match outcome:
        case WaitingOutcome(external_condition=condition, resumes_when=resumes_when):
            line = f"waiting: {condition}; resumes_when={resumes_when}"
        case RetryLaterOutcome(until=until, on_change=on_change):
            resume = f"until={until}" if until is not None else f"on_change={on_change}"
            line = f"retry later: {resume}"
        case NextStrategyOutcome(strategy=strategy):
            if session is not None and (
                session.last_activity_kind is not None
                or session.last_activity_at is not None
                or session.last_duration_seconds is not None
            ):
                duration = (
                    session.last_duration_seconds
                    if session.last_duration_seconds is not None
                    else 0.0
                )
                last_kind = session.last_activity_kind or "none"
                last_at = (
                    f"{session.last_activity_at:.1f}"
                    if isinstance(session.last_activity_at, float)
                    else (session.last_activity_at or "never")
                )
                paths = session.unresolved_paths
                line = (
                    f"{strategy}: duration={duration:.1f}s; "
                    f"last_activity_kind={last_kind}; "
                    f"last_activity_at={last_at}; "
                    f"last_progress=unresolved_count={len(paths)}; "
                    f"unresolved_paths={', '.join(paths)}; "
                    f"next=inspect typed reason, keep landed rebase stops, and retry only if identity changed"
                )
            else:
                line = f"{strategy}: next strategy"
        case ConfigFailureOutcome(file=file, parse_error=parse_error):
            line = f"configuration failure: {file}: {parse_error}"
        case UnknownOutcome(error=error):
            line = f"unknown resolution failure: {error}"
        case _ as unreachable:
            assert_never(unreachable)
    _emit(display, line)


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
