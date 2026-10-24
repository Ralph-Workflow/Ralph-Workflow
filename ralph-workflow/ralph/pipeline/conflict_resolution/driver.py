"""Conflict-resolution driver governed by liveness rather than elapsed time."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import paths_with_conflict_markers, unmerged_paths
from ralph.pipeline._retained_resolution_scope import retained_merge_paths
from ralph.pipeline.conflict_resolution._driver_events import (
    announce_declared_decisions as _announce_declared_decisions,
)
from ralph.pipeline.conflict_resolution._driver_events import (
    emit_attempt_outcome as _emit_attempt_outcome,
)
from ralph.pipeline.conflict_resolution._driver_events import (
    emit_expired_operator_cap as _emit_expired_operator_cap,
)
from ralph.pipeline.conflict_resolution._driver_events import (
    emit_success as _emit_success,
)
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.attempt_fault import (
    INFRASTRUCTURE_TERMINATION_REASONS,
    RESOLVER_NOT_SPENT_TERMINATION_REASONS,
    attempt_fault_to_termination_reason,
    classify_attempt_fault,
)
from ralph.pipeline.conflict_resolution.chain_candidates import (
    next_unvisited as _next_unvisited,
)
from ralph.pipeline.conflict_resolution.chain_candidates import (
    remember_dead_surface as _remember_dead_surface,
)
from ralph.pipeline.conflict_resolution.chain_candidates import (
    skipped_candidates as _skipped_candidates,
)
from ralph.pipeline.conflict_resolution.graph import (
    PHASE_RESOLUTION,
)
from ralph.pipeline.conflict_resolution.invoker import default_invoker as _default_invoker
from ralph.pipeline.conflict_resolution.operator_cap import MonotonicClock
from ralph.pipeline.conflict_resolution.operator_cap import (
    operator_cap_expired as _operator_cap_expired,
)
from ralph.pipeline.conflict_resolution.pipeline_entrypoints import (
    run_conflict_resolution_pipeline,
    run_rebase_conflict_resolution_pipeline,
)
from ralph.pipeline.conflict_resolution.prompt import render_conflict_prompt
from ralph.pipeline.conflict_resolution.rebase_loop import active_rebase_resolution_session
from ralph.pipeline.conflict_resolution.resolution_action import (
    ResolutionAction,
    select_resolution_action,
)
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
from ralph.pipeline.conflict_resolution.round_status import (
    push_round_status as _push_round_status,
)
from ralph.pipeline.conflict_resolution.round_status import (
    restore_previous_status_bar as _restore_status_bar,
)
from ralph.pipeline.conflict_resolution.round_verdict import (
    is_effectively_empty as _is_effectively_empty,
)
from ralph.pipeline.conflict_resolution.round_verdict import (
    reset_round_reporting as _reset_round_reporting,
)
from ralph.pipeline.conflict_resolution.round_verdict import (
    resolution_exhaustion_reason as _resolution_exhaustion_reason,
)
from ralph.pipeline.conflict_resolution.round_verdict import (
    resolution_outcome as _resolution_outcome,
)
from ralph.pipeline.conflict_resolution.round_verdict import (
    round_termination_reason as _round_termination_reason,
)
from ralph.pipeline.conflict_resolution.session import (
    ATTEMPT_FAILED_EVIDENCE,
    ResolutionSession,
    begin_resolution_stop,
    classify_failed_resolution_attempt,
    conflict_chain_max_retries,
    ensure_session_controller,
    invoke_resolution_agent,
    resolution_chain_agents,
)
from ralph.pipeline.conflict_resolution.sight import (
    classify_unmerged_conflicts,
    declared_decision_paths,
    out_of_reach_paths,
    stage_mechanical_conflicts,
)
from ralph.pipeline.conflict_resolution.status import (
    capture_status_bar_model,
    emit_conflict_phase_line,
)
from ralph.pipeline.integration_actions import current_clock
from ralph.pipeline.rebase_state import ResolutionFailureCause

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
    from ralph.pipeline.factory import PipelineDeps
    from ralph.policy.models import PolicyBundle
    from ralph.workspace.scope import WorkspaceScope


type ResolutionInvoker = Callable[[str, "Path", int], ResolutionOutcome | bool]

_QUERY_FAILED_SENTINEL = "<unmerged-path-query-failed>"


@dataclass(frozen=True)
class RoundAttempt:
    """Record success and whether a resolution candidate was invoked."""

    succeeded: bool
    invoked: bool
    #: Whether ANY candidate this round reached a supervised session.
    #: Per-round, not per-invocation: an earlier candidate can repair the
    #: worktree and a later one never start, and the round is judged on
    #: what is on disk by then, so the last invocation's flag would
    #: discard work a different agent had already finished.
    agent_ran: bool = False


def _sleep_seconds(
    seconds: float,
    *,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Wait through the injected clock seam (the real clock in production)."""
    (sleep if sleep is not None else current_clock().sleep)(seconds)


__all__ = [
    "MonotonicClock",
    "ResolutionAction",
    "ResolutionInvoker",
    "RoundAttempt",
    "emit_conflict_phase_line",
    "invoke_resolution_agent",
    "run_conflict_resolution_outcome",
    "run_conflict_resolution_pipeline",
    "run_rebase_conflict_resolution_outcome",
    "run_rebase_conflict_resolution_pipeline",
    "select_resolution_action",
]


def run_conflict_resolution_outcome(
    *,
    root: Path,
    target: str,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps,
    workspace_scope: WorkspaceScope,
    policy_bundle: PolicyBundle,
    display: ParallelDisplay | None,
    display_context: DisplayContext | None,
    invoke: ResolutionInvoker | None = None,
    clock: MonotonicClock | None = None,
    session: ResolutionSession | None = None,
    strategy_history: tuple[str, ...] = (),
) -> ResolutionOutcome:
    """Resolve an in-progress merge through fixed-window liveness supervision."""
    active_session = session or _new_resolution_session(config)
    previous_model = capture_status_bar_model(display)
    try:
        resolved = _run_rounds(
            root=root,
            target=target,
            config=config,
            pipeline_deps=pipeline_deps,
            workspace_scope=workspace_scope,
            policy_bundle=policy_bundle,
            display=display,
            display_context=display_context,
            invoke=invoke,
            clock=clock or time.monotonic,
            stop=None,
            session=active_session,
            strategy_history=strategy_history,
        )
        return _resolution_outcome(active_session, resolved)
    except Exception as exc:
        logger.warning("conflict_resolution: pipeline failed: {}", exc)
        emit_conflict_phase_line(display, f"conflict resolution failed: {exc}")
        active_session.terminal_reason = ResolutionTerminationReason.EXCEPTION
        active_session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
        return _resolution_outcome(active_session, False)
    finally:
        _restore_status_bar(display, root, previous_model)


def run_rebase_conflict_resolution_outcome(
    *,
    root: Path,
    target: str,
    stop: RebaseStop,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps,
    workspace_scope: WorkspaceScope,
    policy_bundle: PolicyBundle,
    display: ParallelDisplay | None,
    display_context: DisplayContext | None,
    invoke: ResolutionInvoker | None = None,
    clock: MonotonicClock | None = None,
    session: ResolutionSession | None = None,
    strategy_history: tuple[str, ...] = (),
) -> ResolutionOutcome:
    """Resolve one paused rebase stop with typed terminal evidence."""
    active_session = (
        session or active_rebase_resolution_session() or _new_resolution_session(config)
    )
    try:
        resolved = _run_rounds(
            root=root,
            target=target,
            config=config,
            pipeline_deps=pipeline_deps,
            workspace_scope=workspace_scope,
            policy_bundle=policy_bundle,
            display=display,
            display_context=display_context,
            invoke=invoke,
            clock=clock or time.monotonic,
            stop=stop,
            session=active_session,
            strategy_history=strategy_history,
        )
        return _resolution_outcome(active_session, resolved)
    except Exception as exc:
        logger.warning("conflict_resolution: rebase stop {} failed: {}", stop.stop_index, exc)
        emit_conflict_phase_line(display, f"rebase conflict resolution failed: {exc}")
        active_session.terminal_reason = ResolutionTerminationReason.EXCEPTION
        active_session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
        return _resolution_outcome(active_session, False)


def _new_resolution_session(config: UnifiedConfig) -> ResolutionSession:
    """Snapshot typed limits once when a standalone merge resolution begins."""
    limits = config.conflict_resolution
    return ResolutionSession(
        inactivity_timeout_seconds=limits.inactivity_timeout_seconds,
        max_rounds_per_stop=limits.max_rounds_per_stop,
        max_rebase_conflict_stops=limits.max_rebase_conflict_stops,
        max_fallback_agents=limits.max_fallback_agents,
        total_resolution_cap_seconds=limits.total_resolution_cap_seconds,
    )


def _prepare_conflicted_paths(
    root: Path,
    stop: RebaseStop | None,
    session: ResolutionSession,
    display: ParallelDisplay | None,
) -> tuple[tuple[str, ...], bool | None, tuple[str, ...]]:
    """Classify on sight; return remaining paths, an early verdict, and decisions."""
    conflicted = (
        stop.conflicted_files
        if stop is not None
        else retained_merge_paths(root, tuple(unmerged_paths(root)))
    )
    if _QUERY_FAILED_SENTINEL in conflicted:
        # Ralph cannot see the conflict. Typed, or this exit reports a
        # failure with no reason at all and the merge seam then calls it
        # "conflict resolution failed".
        session.terminal_reason = ResolutionTerminationReason.OUT_OF_REACH
        session.last_failure_cause = ResolutionFailureCause.CONTEXT_FAILURE
        session.unresolved_paths = tuple(conflicted)
        session.exhaustion_reason = "OUT_OF_REACH: the conflicted paths could not be read"
        emit_conflict_phase_line(
            display, "conflicted paths unreadable; nothing a resolver can repair"
        )
        return (), False, ()
    if not conflicted:
        # Nothing unmerged: there is no conflict to resolve, which is not
        # a failure of anything and must not be reported as one.
        emit_conflict_phase_line(display, "no conflicted paths; nothing for a resolver to repair")
        return (), False, ()
    session.out_of_reach_paths = ()
    kinds = classify_unmerged_conflicts(root, conflicted)
    unreachable = out_of_reach_paths(kinds)
    reachable = tuple(path for path in conflicted if path not in set(unreachable))
    if unreachable and not reachable:
        # Nothing here an agent could carry out. Escalate on sight rather
        # than spending the chain to prove it.
        session.terminal_reason = ResolutionTerminationReason.OUT_OF_REACH
        session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
        session.unresolved_paths = unreachable
        session.exhaustion_reason = _resolution_exhaustion_reason(session, unreachable)
        emit_conflict_phase_line(
            display,
            "OUT_OF_REACH: escalating on sight without spending the chain; unresolved_paths="
            + ", ".join(unreachable),
        )
        return (), False, ()
    if unreachable:
        # Escalating the WHOLE set on one unreachable path meant an
        # ordinary text conflict beside a submodule pointer was never
        # offered to anyone -- on every run, forever, because nothing
        # about the repository changed in between. The reachable paths
        # are resolved; the rest is escalated afterwards, named.
        session.out_of_reach_paths = unreachable
        emit_conflict_phase_line(
            display,
            "OUT_OF_REACH: no resolver can repair "
            + ", ".join(unreachable)
            + "; resolving the rest first",
        )
        conflicted = reachable
    decisions = tuple(path for path in declared_decision_paths(kinds) if path in set(conflicted))
    staged = stage_mechanical_conflicts(root, kinds)
    remaining = tuple(path for path in conflicted if path not in set(staged))
    if remaining:
        return remaining, None, decisions
    if session.out_of_reach_paths:
        # Staging the mechanical paths emptied the REACHABLE set, but a
        # path nobody can repair is still unmerged. Returning success
        # here reported `unresolved_paths=()` for a conflict the driver
        # had just classified unrepairable, invoked nobody, and let the
        # seam commit it -- silently taking one side of a submodule bump.
        _escalate_unreachable_remainder(session, display)
        return (), False, ()
    emit_conflict_phase_line(
        display, "mechanical conflicts staged without spending the resolution chain"
    )
    return (), True, ()


def _render_round_prompt(
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
    # Keep legacy monkeypatched render seams callable until history exists.
    if strategy_history:
        return render_conflict_prompt(
            root=root,
            target=target,
            conflicted_paths=conflicted,
            round_index=round_index,
            round_cap=round_cap,
            surviving_marker_paths=() if round_index == 1 else session.unresolved_paths,
            replaying_commit_sha=stop.sha if stop is not None else None,
            replaying_commit_subject=stop.subject if stop is not None else None,
            stop_index=stop.stop_index if stop is not None else None,
            stop_cap=stop.stop_cap if stop is not None else None,
            strategy_history=strategy_history,
        )
    return render_conflict_prompt(
        root=root,
        target=target,
        conflicted_paths=conflicted,
        round_index=round_index,
        round_cap=round_cap,
        surviving_marker_paths=() if round_index == 1 else session.unresolved_paths,
        replaying_commit_sha=stop.sha if stop is not None else None,
        replaying_commit_subject=stop.subject if stop is not None else None,
        stop_index=stop.stop_index if stop is not None else None,
        stop_cap=stop.stop_cap if stop is not None else None,
    )


def _evaluate_round_attempt(
    attempt: RoundAttempt,
    session: ResolutionSession,
    display: ParallelDisplay | None,
    round_index: int,
    round_cap: int,
    attempt_started: float,
    clock: MonotonicClock,
    decision_paths: tuple[str, ...],
) -> ResolutionAction:
    succeeded = attempt.succeeded
    agent_ran = attempt.invoked and attempt.agent_ran
    proved_by_worktree = agent_ran and not decision_paths
    resolved_now = (succeeded or proved_by_worktree) and not session.unresolved_paths
    action = select_resolution_action(session, round_index, round_cap, resolved=resolved_now)
    round_reason = (
        None if resolved_now else _round_termination_reason(session, invoked=attempt.invoked)
    )
    session.terminal_reason = round_reason
    outcome = ResolutionOutcome(
        succeeded=resolved_now,
        reason=round_reason,
        duration_seconds=(
            session.last_duration_seconds
            if session.last_duration_seconds is not None
            else max(0.0, clock() - attempt_started)
        ),
        last_activity_kind=session.last_activity_kind,
        last_activity_at=session.last_activity_at,
        unresolved_paths=session.unresolved_paths,
        failure_cause=(
            ResolutionFailureCause.NONE
            if resolved_now
            else (
                session.last_failure_cause
                if session.last_failure_cause != ResolutionFailureCause.NONE
                else (
                    ResolutionFailureCause.AGENT_FAILURE
                    if round_reason in RESOLVER_NOT_SPENT_TERMINATION_REASONS
                    else ResolutionFailureCause.UNRESOLVED_CONFLICT
                )
            )
        ),
    )
    _emit_attempt_outcome(display, outcome)
    return action


def _run_rounds(
    *,
    root: Path,
    target: str,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps,
    workspace_scope: WorkspaceScope,
    policy_bundle: PolicyBundle,
    display: ParallelDisplay | None,
    display_context: DisplayContext | None,
    invoke: ResolutionInvoker | None,
    clock: MonotonicClock,
    stop: RebaseStop | None,
    session: ResolutionSession,
    strategy_history: tuple[str, ...],
) -> bool:
    """Execute completed-work routing while one session owns all timing context."""
    limits = config.conflict_resolution
    round_cap = session.max_rounds_per_stop or limits.max_rounds_per_stop
    inactivity_timeout = session.inactivity_timeout_seconds or limits.inactivity_timeout_seconds
    is_rotation = bool(strategy_history)
    # Before anything can return: every early exit below reports through
    # the session, and an unreset session reports the LAST conflict's
    # verdict for a stop that never invoked anybody.
    begin_resolution_stop(session, is_strategy_rotation=is_rotation)
    if pipeline_deps is not None and session.unavailability_store is None:
        session.unavailability_store = pipeline_deps.unavailability_store
    ensure_session_controller(session, policy_bundle)
    prepared, early, decision_paths = _prepare_conflicted_paths(root, stop, session, display)
    if early is not None:
        return early
    conflicted = prepared
    _announce_declared_decisions(display, decision_paths)
    candidates = resolution_chain_agents(policy_bundle)
    if not candidates:
        _record_unconfigured_resolver(session, conflicted, display)
        return False
    if session.started_at is None:
        session.started_at = clock()
    session.unresolved_paths = conflicted
    runner = invoke or _default_invoker(
        config=config,
        pipeline_deps=pipeline_deps,
        workspace_scope=workspace_scope,
        policy_bundle=policy_bundle,
        display=display,
        display_context=display_context,
        limits=limits,
        round_cap=round_cap,
        inactivity_timeout=inactivity_timeout,
        clock=clock,
        session=session,
        stop=stop,
        root=root,
        target=target,
        require_completion_evidence=bool(decision_paths),
    )
    emit_conflict_phase_line(
        display,
        f"entering rebase conflict resolution for '{target}' ({len(conflicted)} conflicted file(s))"
        + (f" replaying {stop.sha[:8]} {stop.subject}" if stop is not None else ""),
    )
    # Snapshotted once for the STOP, not per round: a file deleted in
    # round 1 is already absent when round 2 starts, so a per-round
    # snapshot stopped calling it vanished -- and the empty marker scan
    # (it cannot read a file that is gone) then credited the deletion.
    present_at_entry = frozenset(path for path in conflicted if (root / path).exists())
    substantive_at_entry = frozenset(
        path for path in present_at_entry if not _is_effectively_empty(root / path)
    )
    resolved = _execute_round_loop(
        root=root,
        target=target,
        runner=runner,
        candidates=candidates,
        session=session,
        display=display,
        conflicted=conflicted,
        decision_paths=decision_paths,
        present_at_entry=present_at_entry,
        substantive_at_entry=substantive_at_entry,
        round_cap=round_cap,
        clock=clock,
        stop=stop,
        strategy_history=strategy_history,
        policy_bundle=policy_bundle,
    )
    if resolved:
        return True
    # NOT `or conflicted`: an empty scan means the markers are gone, and
    # falling back to the original list named paths the scan had just
    # cleared -- directly contradicting the line above it, which reported
    # `unresolved_count=0`.
    unresolved = session.unresolved_paths
    session.exhaustion_reason = _resolution_exhaustion_reason(session, unresolved)
    emit_conflict_phase_line(
        display,
        "abandoning conflict resolution; " + session.exhaustion_reason,
    )
    return False


def _execute_round_loop(
    *,
    root: Path,
    target: str,
    runner: ResolutionInvoker,
    candidates: tuple[str, ...],
    session: ResolutionSession,
    display: ParallelDisplay | None,
    conflicted: tuple[str, ...],
    decision_paths: tuple[str, ...],
    present_at_entry: frozenset[str],
    substantive_at_entry: frozenset[str],
    round_cap: int,
    clock: MonotonicClock,
    stop: RebaseStop | None,
    strategy_history: tuple[str, ...],
    policy_bundle: PolicyBundle,
) -> bool:
    prompt_path: Path | None = None
    try:
        start_round = session.rounds_spent + 1
        for round_index in range(start_round, round_cap + 1):
            session.rounds_spent = round_index
            if _operator_cap_expired(session, clock):
                _emit_expired_operator_cap(display, session, conflicted, clock)
                return False
            _push_round_status(display, root, target, round_index, round_cap, stop)
            prompt_path = _render_round_prompt(
                root, target, conflicted, round_index, round_cap, session, stop, strategy_history
            )
            if prompt_path is None:
                session.terminal_reason = ResolutionTerminationReason.PROMPT_UNAVAILABLE
                session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
                session.exhaustion_reason = _resolution_exhaustion_reason(session, conflicted)
                emit_conflict_phase_line(display, "could not materialize the resolution prompt")
                return False
            attempt_started = clock()
            _reset_round_reporting(session)
            attempt = _run_one_round(
                runner,
                candidates,
                prompt_path,
                round_index,
                display,
                session,
                policy_bundle,
                worktree_resolved=lambda: (
                    not decision_paths
                    and not _paths_still_unresolved(
                        root,
                        conflicted,
                        decision_paths,
                        present_before=present_at_entry,
                        substantive=substantive_at_entry,
                    )
                ),
            )
            session.unresolved_paths = _paths_still_unresolved(
                root,
                conflicted,
                decision_paths,
                present_before=present_at_entry,
                substantive=substantive_at_entry,
            )
            action = _evaluate_round_attempt(
                attempt,
                session,
                display,
                round_index,
                round_cap,
                attempt_started,
                clock,
                decision_paths,
            )
            if action in (
                ResolutionAction.PROCEED_TO_FAST_FORWARD,
                ResolutionAction.ACCEPT_WORKTREE_REPAIR,
            ):
                if _escalate_unreachable_remainder(session, display):
                    break
                _emit_success(display, round_index, stop)
                return True
            if not attempt.invoked:
                _handle_dead_candidate_chain(session, conflicted, display)
                break
            if action == ResolutionAction.ESCALATE_TO_INTERVENTION:
                _escalate_unreachable_remainder(session, display)
                break
    finally:
        if prompt_path is not None:
            with contextlib.suppress(OSError):
                prompt_path.unlink()
    return False


def _record_unconfigured_resolver(
    session: ResolutionSession,
    conflicted: tuple[str, ...],
    display: ParallelDisplay | None,
) -> None:
    """Type an exit that would otherwise carry no reason at all.

    Without it the caller records "conflict resolution failed" for a
    resolution that was never configured to happen.
    """
    session.terminal_reason = ResolutionTerminationReason.NO_RESOLVER_CONFIGURED
    session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
    session.unresolved_paths = conflicted
    session.exhaustion_reason = _resolution_exhaustion_reason(session, conflicted)
    emit_conflict_phase_line(display, "no agent bound to the rebase-conflict-resolution drain")


def _handle_dead_candidate_chain(
    session: ResolutionSession,
    conflicted: tuple[str, ...],
    display: ParallelDisplay | None,
) -> None:
    """Record that every candidate is dead and cannot spend a round."""
    session.terminal_reason = ResolutionTerminationReason.TOOL_SURFACE_DEAD
    session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
    session.unresolved_paths = session.unresolved_paths or conflicted
    emit_conflict_phase_line(
        display,
        "every resolution candidate is a dead tool surface; no further round can spend one",
    )


def _escalate_unreachable_remainder(
    session: ResolutionSession, display: ParallelDisplay | None
) -> bool:
    """Report paths no resolver can repair after the rest were resolved."""
    if not session.out_of_reach_paths:
        return False
    session.terminal_reason = ResolutionTerminationReason.OUT_OF_REACH
    session.unresolved_paths = session.out_of_reach_paths
    emit_conflict_phase_line(
        display,
        "the resolvable conflicts are repaired, but no resolver can repair "
        + ", ".join(session.out_of_reach_paths),
    )
    return True


def _paths_still_unresolved(
    root: Path,
    conflicted: tuple[str, ...],
    decision_paths: tuple[str, ...],
    *,
    present_before: frozenset[str],
    substantive: frozenset[str],
) -> tuple[str, ...]:
    """Paths a round has NOT resolved, by the evidence on disk.

    Surviving markers are the usual proof, but the marker scan skips a
    path it cannot read -- so a file the resolver DELETED passed it
    vacuously, and a two-sided content conflict was reported resolved by
    dropping both sides. A declared decision may legitimately end in a
    deletion; an ordinary conflict may not, because neither side asked
    for the file to disappear.
    """
    marked = list(paths_with_conflict_markers(root, conflicted))
    decisions = set(decision_paths)
    # A path both sides wrote content into, now empty, is not a
    # resolution: a truncating resolver dropped BOTH sides, and with the
    # markers gone the scan had nothing left to object to.
    emptied = [
        path
        for path in conflicted
        if path in substantive and path not in marked and _is_effectively_empty(root / path)
    ]
    vanished = [
        path
        for path in conflicted
        if path in present_before
        and path not in decisions
        and path not in marked
        and not (root / path).exists()
    ]
    unresolved: list[str] = []
    for path in (*marked, *emptied, *vanished):
        if path not in unresolved:
            unresolved.append(path)
    return tuple(unresolved)


def _record_invoker_outcome(
    session: ResolutionSession,
    invoker_result: ResolutionOutcome,
) -> None:
    session.last_failure_cause = invoker_result.failure_cause
    if invoker_result.reason is not None:
        session.terminal_reason = invoker_result.reason
    if invoker_result.last_activity_kind is not None:
        session.last_activity_kind = invoker_result.last_activity_kind
    if invoker_result.last_activity_at is not None:
        session.last_activity_at = invoker_result.last_activity_at
    if invoker_result.duration_seconds > 0:
        session.last_duration_seconds = invoker_result.duration_seconds
    if invoker_result.unresolved_paths:
        session.unresolved_paths = invoker_result.unresolved_paths
    if invoker_result.failure_cause == ResolutionFailureCause.AGENT_FAILURE:
        session.charge_conflict_budget = False


def _handle_candidate_failure(
    session: ResolutionSession,
    agent_name: str,
    invoker_result: ResolutionOutcome | bool,
    candidates: tuple[str, ...],
    offset: int,
    policy_bundle: PolicyBundle,
) -> None:
    if not isinstance(invoker_result, ResolutionOutcome):
        fault = classify_attempt_fault(session.last_attempt_evidence)
        if session.terminal_reason is None:
            session.terminal_reason = (
                attempt_fault_to_termination_reason(fault, session.last_attempt_evidence)
                or ResolutionTerminationReason.ATTEMPT_FAILED
            )
        if session.terminal_reason in RESOLVER_NOT_SPENT_TERMINATION_REASONS:
            session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
        else:
            session.last_failure_cause = ResolutionFailureCause.UNRESOLVED_CONFLICT
    classify_failed_resolution_attempt(
        session,
        agent_name,
        session.last_attempt_failure or session.last_attempt_evidence or ATTEMPT_FAILED_EVIDENCE,
        candidates=candidates,
        failed_index=offset,
        policy_bundle=policy_bundle,
        bench=(
            session.terminal_reason in RESOLVER_NOT_SPENT_TERMINATION_REASONS
            and session.terminal_reason != ResolutionTerminationReason.ATTEMPT_FAILED
        ),
    )


def _execute_single_candidate(
    runner: ResolutionInvoker,
    agent_name: str,
    prompt_path: Path,
    round_index: int,
    session: ResolutionSession,
    candidates: tuple[str, ...],
    offset: int,
    policy_bundle: PolicyBundle,
) -> tuple[bool, ResolutionOutcome | bool]:
    session.terminal_reason = None
    session.charge_conflict_budget = True
    invoker_result: ResolutionOutcome | bool = False
    try:
        invoker_result = runner(agent_name, prompt_path, round_index)
        if isinstance(invoker_result, ResolutionOutcome):
            if invoker_result.succeeded:
                session.chain_cursor = 0
                session.last_failure_cause = ResolutionFailureCause.NONE
                _reset_conflict_success(session, agent_name)
                return True, invoker_result
            _record_invoker_outcome(session, invoker_result)
        elif invoker_result:
            session.chain_cursor = 0
            session.last_failure_cause = ResolutionFailureCause.NONE
            _reset_conflict_success(session, agent_name)
            return True, invoker_result
    except Exception as exc:
        logger.warning(
            "conflict_resolution: round {} with '{}' raised: {}", round_index, agent_name, exc
        )
        session.terminal_reason = ResolutionTerminationReason.EXCEPTION
        session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
        classify_failed_resolution_attempt(
            session,
            agent_name,
            exc,
            candidates=candidates,
            failed_index=offset,
            policy_bundle=policy_bundle,
            bench=True,
        )
        return False, False
    else:
        _handle_candidate_failure(
            session, agent_name, invoker_result, candidates, offset, policy_bundle
        )
        return False, invoker_result


def _check_worktree_repair(
    session: ResolutionSession,
    agent_name: str,
    round_index: int,
    attempt_cap: int,
    display: ParallelDisplay | None,
    worktree_resolved: Callable[[], bool] | None,
    agent_saw: bool,
) -> bool:
    if not (worktree_resolved is not None and agent_saw and worktree_resolved()):
        return False
    session.unresolved_paths = ()
    if (
        select_resolution_action(session, round_index, attempt_cap)
        == ResolutionAction.ACCEPT_WORKTREE_REPAIR
    ):
        emit_conflict_phase_line(
            display,
            f"round {round_index}: {agent_name} ended badly but the worktree shows "
            "every marker repaired; not spending another candidate",
        )
        return True
    return False


def _run_one_round(
    runner: ResolutionInvoker,
    candidates: tuple[str, ...],
    prompt_path: Path,
    round_index: int,
    display: ParallelDisplay | None,
    session: ResolutionSession,
    policy_bundle: PolicyBundle,
    worktree_resolved: Callable[[], bool] | None = None,
) -> RoundAttempt:
    """Spend eligible candidates in priority order, respecting bounded retries.

    A successful candidate does not advance priority. Cooldowns survive
    rounds and stops; the controller selects again before every invocation.
    ``worktree_resolved`` rechecks repairs after an unsuccessful attempt.

    Clear terminal evidence before each candidate to preserve attribution.
    """
    total = len(candidates)
    if total == 0:
        return RoundAttempt(succeeded=False, invoked=False)
    skipped = _skipped_candidates(session, candidates)
    if len(skipped) == total and len(session.dead_tool_surfaces) < total:
        # Every remaining candidate is barred by a fault of RALPH's, not
        # by anything about the agents. Doing nothing is the one outcome
        # that cannot resolve the conflict, so the stop-scoped bar is
        # dropped and the chain is spent again rather than skipped.
        emit_conflict_phase_line(
            display,
            "every candidate is barred by an earlier infrastructure fault; retrying "
            + ", ".join(session.stop_dead_surfaces)
            + " rather than leaving the conflict unresolved",
        )
        session.stop_dead_surfaces = ()
        skipped = _skipped_candidates(session, candidates)
    visited: set[int] = set()
    # Bound same-agent retries independently of candidate count.
    attempt_cap = max(1, conflict_chain_max_retries(policy_bundle))
    attempts_here = 0
    invoked = False
    agent_ran = False
    while len(visited) < total:
        selected = _preferred_conflict_candidate(session, candidates, visited, skipped)
        if selected is None:
            return RoundAttempt(succeeded=False, invoked=invoked, agent_ran=agent_ran)
        offset = selected
        agent_name = candidates[offset]
        emit_conflict_phase_line(
            display, f"round {round_index}: invoking {agent_name} to resolve the conflicts"
        )
        invoked = True
        succeeded, invoker_result = _execute_single_candidate(
            runner,
            agent_name,
            prompt_path,
            round_index,
            session,
            candidates,
            offset,
            policy_bundle,
        )
        if succeeded:
            return RoundAttempt(succeeded=True, invoked=True, agent_ran=True)
        agent_saw = session.last_attempt_saw_activity or (
            isinstance(invoker_result, ResolutionOutcome)
            and (
                invoker_result.last_activity_kind is not None or invoker_result.duration_seconds > 0
            )
        )
        agent_ran = agent_ran or agent_saw
        if _check_worktree_repair(
            session, agent_name, round_index, attempt_cap, display, worktree_resolved, agent_saw
        ):
            return RoundAttempt(succeeded=False, invoked=True, agent_ran=True)
        if session.terminal_reason in INFRASTRUCTURE_TERMINATION_REASONS:
            _remember_dead_surface(session, agent_name)
            skipped = _skipped_candidates(session, candidates)
            session.charge_conflict_budget = False
            session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
            visited.add(offset)
            offset = _next_unvisited(offset, total, visited)
            session.chain_cursor = offset
            continue
        attempts_here += 1
        if session.chain_cursor == offset and attempts_here < attempt_cap:
            _sleep_conflict_retry(session, policy_bundle)
            continue
        visited.add(offset)
        proposed = session.chain_cursor % total
        offset = proposed if proposed not in visited else _next_unvisited(offset, total, visited)
        session.chain_cursor = offset
        attempts_here = 0
    return RoundAttempt(succeeded=False, invoked=invoked, agent_ran=agent_ran)


def _preferred_conflict_candidate(
    session: ResolutionSession,
    candidates: tuple[str, ...],
    visited: set[int],
    skipped: frozenset[str],
) -> int | None:
    """Select by priority among this round's remaining eligible candidates."""
    from ralph.recovery.controller import RecoveryController

    eligible = [
        name
        for index, name in enumerate(candidates)
        if index not in visited and name not in skipped
    ]
    if not eligible:
        return None
    controller = session.recovery_controller
    if not isinstance(controller, RecoveryController):
        return candidates.index(eligible[0])
    selection = controller.preferred_agent_index(PHASE_RESOLUTION, eligible)
    if selection.agent is not None:
        return candidates.index(selection.agent)
    delay = controller.earliest_available_wait_ms(PHASE_RESOLUTION, eligible)
    if delay <= 0:
        return None
    # Wait out the earliest cooldown ONCE, then go on with the candidate that
    # was closest to returning. Looping until the ledger says "available" would
    # spin forever whenever the sleep does not actually advance the clock.
    _sleep_seconds(delay / 1000.0)
    selection = controller.preferred_agent_index(PHASE_RESOLUTION, eligible)
    if selection.agent is not None:
        return candidates.index(selection.agent)

    def remaining_wait_ms(name: str) -> int:
        return controller.earliest_available_wait_ms(PHASE_RESOLUTION, [name])

    return candidates.index(min(eligible, key=remaining_wait_ms))


def _reset_conflict_success(session: ResolutionSession, agent: str) -> None:
    """Reset only the successful resolver's cooldown history."""
    from ralph.recovery.controller import RecoveryController

    if isinstance(session.recovery_controller, RecoveryController):
        session.recovery_controller.reset_backoff(PHASE_RESOLUTION, agent)


def _sleep_conflict_retry(session: ResolutionSession, policy_bundle: PolicyBundle) -> None:
    """Honor RecoveryController backoff, falling back to the chain's retry_delay_ms."""
    delay_ms = session.last_retry_delay_ms
    if delay_ms <= 0:
        drain = policy_bundle.agents.agent_drains.get(PHASE_RESOLUTION)
        chain = policy_bundle.agents.agent_chains.get(drain.chain) if drain is not None else None
        if chain is not None:
            delay_ms = chain.retry_delay_ms
    if delay_ms > 0:
        _sleep_seconds(delay_ms / 1000.0)
