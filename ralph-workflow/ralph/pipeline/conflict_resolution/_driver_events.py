from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from ralph._typed_attrs import read_attr
from ralph.pipeline._integration_status import build_integration_status, cooldown_next_eligible
from ralph.pipeline.conflict_resolution._operator_command import operator_command_for_paths
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
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
from ralph.pipeline.state import PipelineState

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline._integration_status import IntegrationStatus
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
    from ralph.pipeline.conflict_resolution.session import ResolutionSession


class _DriverEventsState:
    last_waiting_key: tuple[str, ...] | None = None


_state = _DriverEventsState()


def reset_driver_events_state() -> None:
    _state.last_waiting_key = None


def _emit(display: ParallelDisplay | None, line: str) -> None:
    from ralph.pipeline.conflict_resolution import driver as driver_mod

    fn: object = read_attr(driver_mod, "emit_conflict_phase_line", None)
    if callable(fn):
        fn(display, line)
        return
    emit_conflict_phase_line(display, line)


def _chain_agents(session: ResolutionSession | None) -> tuple[str, ...]:
    if session is None:
        return ()
    controller = session.recovery_controller
    if controller is not None:
        agents = controller.agents_for_phase(PHASE_RESOLUTION)
        if agents:
            return tuple(agents)
    recovery_state = session.recovery_state
    if not isinstance(recovery_state, PipelineState):
        return ()
    chain = recovery_state.chain_for_phase(PHASE_RESOLUTION)
    if chain is None:
        return ()
    return tuple(chain.agents)


def _cooldown_reasons(session: ResolutionSession | None) -> tuple[str, ...]:
    if session is None:
        return ()
    controller = session.recovery_controller
    if controller is None:
        return ()
    reasons: list[str] = []
    for agent, status in controller.chain_eligibility(PHASE_RESOLUTION, _chain_agents(session)):
        if status.is_eligible:
            continue
        reasons.append(f"{agent}: {status.reason or 'cooldown'}")
    return tuple(reasons)


def _progress_counts(session: ResolutionSession | None) -> tuple[int | None, int | None]:
    if session is None:
        return (None, None)
    initial = session.initial_conflicted_count
    remaining = len(session.unresolved_paths)
    done = None if initial is None else max(initial - remaining, 0)
    return (done, remaining)


def _session_status(
    session: ResolutionSession | None,
    *,
    cause: str,
    path_reason: str,
    agents_can_finish: bool,
    strategy: str | None = None,
) -> IntegrationStatus:
    paths = tuple(session.unresolved_paths) if session is not None else ()
    done, remaining = _progress_counts(session)
    agent, until_when = cooldown_next_eligible(
        session.recovery_controller if session is not None else None,
        PHASE_RESOLUTION,
        _chain_agents(session),
    )
    return build_integration_status(
        cause=cause,
        paths_with_reasons=tuple((path, path_reason) for path in paths),
        command=operator_command_for_paths(paths),
        agents_can_finish=agents_can_finish,
        agent_reasons=_cooldown_reasons(session),
        done_count=done,
        remaining_count=remaining,
        strategy=strategy,
        next_eligible_agent=agent,
        until_when=until_when,
    )


def _waiting_change_key(status: IntegrationStatus) -> tuple[str, ...]:
    return status.change_key


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
        _state.last_waiting_key = None
        return
    if session is not None and (
        session.terminal_reason is not None
        or session.last_activity_kind is not None
        or session.last_duration_seconds is not None
    ):
        _state.last_waiting_key = None
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
        status = _session_status(
            session,
            cause=reason_label,
            path_reason="unresolved conflict",
            agents_can_finish=False,
        )
        line = (
            f"{status.summary}; "
            f"duration={duration:.1f}s; "
            f"last_activity_kind={session.last_activity_kind or 'none'}; "
            f"last_activity_at={session.last_activity_at or 'never'}; "
            "next=inspect typed reason, keep landed rebase stops, "
            "and retry only if identity changed"
        )
        _emit(display, line)
        return
    match outcome:
        case WaitingOutcome(external_condition=condition, resumes_when=resumes_when):
            status = _session_status(
                session,
                cause=f"WAITING: {condition}; resumes_when={resumes_when}",
                path_reason="unresolved conflict",
                agents_can_finish=False,
            )
            key = _waiting_change_key(status)
            if key == _state.last_waiting_key:
                return
            _state.last_waiting_key = key
            line = status.render_log
        case RetryLaterOutcome(until=until, on_change=on_change):
            _state.last_waiting_key = None
            resume = f"until={until}" if until is not None else f"on_change={on_change}"
            line = f"retry later: {resume}"
        case NextStrategyOutcome(strategy=strategy):
            _state.last_waiting_key = None
            status = _session_status(
                session,
                cause=strategy,
                path_reason="unresolved conflict",
                agents_can_finish=True,
                strategy=strategy,
            )
            line = status.render_log
        case ConfigFailureOutcome(file=file, parse_error=parse_error):
            _state.last_waiting_key = None
            line = f"configuration failure: {file}: {parse_error}"
        case UnknownOutcome(error=error):
            _state.last_waiting_key = None
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
    auto_merged = stop.auto_merged_files if stop is not None else ()
    if strategy_history:
        return render_fn(
            root=root,
            target=target,
            conflicted_paths=conflicted,
            auto_merged_paths=auto_merged,
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
        auto_merged_paths=auto_merged,
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
    "emit_success",
    "render_round_prompt",
    "reset_driver_events_state",
]
