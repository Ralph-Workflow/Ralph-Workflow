"""Conflict-resolution driver governed by liveness rather than elapsed time."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import paths_with_conflict_markers, unmerged_paths
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.attempt_fault import INFRASTRUCTURE_TERMINATION_REASONS
from ralph.pipeline.conflict_resolution.graph import (
    PHASE_RESOLUTION,
    TERMINAL_RESOLVED,
    route_after_round,
)
from ralph.pipeline.conflict_resolution.prompt import render_conflict_prompt
from ralph.pipeline.conflict_resolution.rebase_loop import active_rebase_resolution_session
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
from ralph.pipeline.conflict_resolution.session import (
    ATTEMPT_FAILED_EVIDENCE,
    ResolutionSession,
    begin_resolution_stop,
    classify_failed_resolution_attempt,
    conflict_chain_max_retries,
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
    ResolutionStatusReporter,
    capture_status_bar_model,
    clear_conflict_status_bar,
    emit_conflict_phase_line,
    push_conflict_status_bar,
    restore_status_bar,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.conflict_resolution_config import ConflictResolutionConfig
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
    from ralph.pipeline.factory import PipelineDeps
    from ralph.policy.models import PolicyBundle
    from ralph.workspace.scope import WorkspaceScope


type ResolutionInvoker = Callable[[str, "Path", int], bool]
type MonotonicClock = Callable[[], float]

_QUERY_FAILED_SENTINEL = "<unmerged-path-query-failed>"


@dataclass(frozen=True)
class RoundAttempt:
    """What one resolution round actually did.

    ``invoked`` is the half the terminal reason depends on: a round that
    spent no candidate has no resolver verdict to report, so the driver
    must not describe it as one.
    """

    succeeded: bool
    invoked: bool
    #: Whether ANY candidate this round reached a supervised session.
    #: Per-round, not per-invocation: an earlier candidate can repair the
    #: worktree and a later one never start, and the round is judged on
    #: what is on disk by then, so the last invocation's flag would
    #: discard work a different agent had already finished.
    agent_ran: bool = False


def _sleep_seconds(seconds: float) -> None:
    """Sleep without exposing ``time.sleep`` as the patch target for tests."""
    time.sleep(seconds)  # filesystem-poll-ok: RecoveryController chain retry_delay_ms backoff

__all__ = [
    "MonotonicClock",
    "ResolutionInvoker",
    "RoundAttempt",
    "run_conflict_resolution_outcome",
    "run_conflict_resolution_pipeline",
    "run_rebase_conflict_resolution_outcome",
    "run_rebase_conflict_resolution_pipeline",
]


def _resolution_outcome(session: ResolutionSession, succeeded: bool) -> ResolutionOutcome:
    """Expose one resolver invocation as typed outcome evidence."""
    return ResolutionOutcome(
        succeeded=succeeded,
        reason=None if succeeded else session.terminal_reason,
        duration_seconds=session.last_duration_seconds or 0.0,
        last_activity_kind=session.last_activity_kind,
        last_activity_at=session.last_activity_at,
        unresolved_paths=session.unresolved_paths,
    )


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
        )
        return _resolution_outcome(active_session, resolved)
    except Exception as exc:
        logger.warning("conflict_resolution: pipeline failed: {}", exc)
        emit_conflict_phase_line(display, f"conflict resolution failed: {exc}")
        active_session.terminal_reason = ResolutionTerminationReason.EXCEPTION
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
) -> ResolutionOutcome:
    """Resolve one paused rebase stop with typed terminal evidence."""
    active_session = session or active_rebase_resolution_session() or _new_resolution_session(config)
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
        )
        return _resolution_outcome(active_session, resolved)
    except Exception as exc:
        logger.warning("conflict_resolution: rebase stop {} failed: {}", stop.stop_index, exc)
        emit_conflict_phase_line(display, f"rebase conflict resolution failed: {exc}")
        active_session.terminal_reason = ResolutionTerminationReason.EXCEPTION
        return _resolution_outcome(active_session, False)


def run_conflict_resolution_pipeline(
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
) -> bool:
    """Backward-compatible boolean projection of the typed merge outcome."""
    return run_conflict_resolution_outcome(
        root=root,
        target=target,
        config=config,
        pipeline_deps=pipeline_deps,
        workspace_scope=workspace_scope,
        policy_bundle=policy_bundle,
        display=display,
        display_context=display_context,
        invoke=invoke,
        clock=clock,
        session=session,
    ).succeeded


def run_rebase_conflict_resolution_pipeline(
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
) -> bool:
    """Backward-compatible projection of the out-of-graph ``PHASE_RESOLUTION`` outcome."""
    return run_rebase_conflict_resolution_outcome(
        root=root,
        target=target,
        stop=stop,
        config=config,
        pipeline_deps=pipeline_deps,
        workspace_scope=workspace_scope,
        policy_bundle=policy_bundle,
        display=display,
        display_context=display_context,
        invoke=invoke,
        clock=clock,
        session=session,
    ).succeeded


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


def _restore_status_bar(display: ParallelDisplay | None, root: Path, previous_model: object | None) -> None:
    if previous_model is None:
        clear_conflict_status_bar(
            display,
            root,
            run_started_monotonic=_display_run_started_monotonic(display),
        )
    else:
        restore_status_bar(display, previous_model)


def _prepare_conflicted_paths(
    root: Path,
    stop: RebaseStop | None,
    session: ResolutionSession,
    display: ParallelDisplay | None,
) -> tuple[tuple[str, ...], bool | None, tuple[str, ...]]:
    """Classify on sight; return remaining paths, an early verdict, and decisions."""
    conflicted = stop.conflicted_files if stop is not None else tuple(unmerged_paths(root))
    if not conflicted or _QUERY_FAILED_SENTINEL in conflicted:
        emit_conflict_phase_line(display, "no readable conflicted paths; nothing a resolver can repair")
        return (), False, ()
    kinds = classify_unmerged_conflicts(root, conflicted)
    unreachable = out_of_reach_paths(kinds)
    if unreachable:
        # The WHOLE set escalates, including paths an agent could repair.
        # That is deliberate: a stop holding an unrepairable path cannot
        # complete, so it is aborted, and any partial resolution beside it
        # is discarded with it. Spending an agent session on work that is
        # certain to be thrown away costs the operator money and buys
        # nothing.
        session.terminal_reason = ResolutionTerminationReason.OUT_OF_REACH
        session.exhaustion_reason = _resolution_exhaustion_reason(session, unreachable)
        emit_conflict_phase_line(
            display,
            "OUT_OF_REACH: escalating on sight without spending the chain; unresolved_paths="
            + ", ".join(unreachable),
        )
        return (), False, ()
    decisions = declared_decision_paths(kinds)
    staged = stage_mechanical_conflicts(root, kinds)
    if not staged:
        return conflicted, None, decisions
    remaining = tuple(path for path in conflicted if path not in set(staged))
    if remaining:
        return remaining, None, decisions
    emit_conflict_phase_line(
        display, "mechanical conflicts staged without spending the resolution chain"
    )
    return (), True, ()


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
) -> bool:
    """Execute completed-work routing while one session owns all timing context."""
    limits = config.conflict_resolution
    round_cap = session.max_rounds_per_stop or limits.max_rounds_per_stop
    inactivity_timeout = session.inactivity_timeout_seconds or limits.inactivity_timeout_seconds
    # Before anything can return: every early exit below reports through
    # the session, and an unreset session reports the LAST conflict's
    # verdict for a stop that never invoked anybody.
    begin_resolution_stop(session)
    prepared, early, decision_paths = _prepare_conflicted_paths(root, stop, session, display)
    if early is not None:
        return early
    conflicted = prepared
    if decision_paths:
        emit_conflict_phase_line(
            display,
            "markerless conflict(s) need a declared decision (keep the edit or accept "
            f"the deletion): {', '.join(decision_paths)}",
        )
    candidates = resolution_chain_agents(policy_bundle)
    if not candidates:
        # Typed, because this exit is otherwise reason-less: the caller
        # then records "conflict resolution failed" for a resolution that
        # was never configured to happen.
        session.terminal_reason = ResolutionTerminationReason.NO_RESOLVER_CONFIGURED
        session.exhaustion_reason = _resolution_exhaustion_reason(session, conflicted)
        emit_conflict_phase_line(display, "no agent bound to the rebase-conflict-resolution drain")
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
    prompt_path: Path | None = None
    try:
        for round_index in range(1, round_cap + 1):
            if _operator_cap_expired(session, clock):
                _emit_expired_operator_cap(display, session, conflicted, clock)
                return False
            _push_round_status(display, root, target, round_index, round_cap, stop)
            prompt_path = render_conflict_prompt(
                root=root,
                target=target,
                conflicted_paths=conflicted,
                round_index=round_index,
                round_cap=round_cap,
                surviving_marker_paths=(
                    () if round_index == 1 else session.unresolved_paths
                ),
                replaying_commit_sha=stop.sha if stop is not None else None,
                replaying_commit_subject=stop.subject if stop is not None else None,
                stop_index=stop.stop_index if stop is not None else None,
                stop_cap=stop.stop_cap if stop is not None else None,
            )
            if prompt_path is None:
                session.terminal_reason = ResolutionTerminationReason.PROMPT_UNAVAILABLE
                session.exhaustion_reason = _resolution_exhaustion_reason(session, conflicted)
                emit_conflict_phase_line(display, "could not materialize the resolution prompt")
                return False
            attempt_started = clock()
            if (
                session.terminal_reason not in INFRASTRUCTURE_TERMINATION_REASONS
                and session.terminal_reason is not ResolutionTerminationReason.EXCEPTION
            ):
                session.terminal_reason = None
            session.last_activity_kind = None
            session.last_activity_at = None
            session.last_duration_seconds = None
            attempt = _run_one_round(
                runner, candidates, prompt_path, round_index, display, session, policy_bundle
            )
            succeeded = attempt.succeeded
            session.unresolved_paths = tuple(paths_with_conflict_markers(root, conflicted))
            # The worktree decides, but only once an agent has actually
            # been at it. A resolver that repaired every marker and then
            # ended its session badly still repaired every marker, and
            # the rebase loop re-proves it (stage, re-scan, no unmerged
            # paths) before anything lands -- so believing the exit
            # status over the evidence only threw finished work away and
            # sent the same conflict round after round. A round that
            # never reached a supervised agent still proves nothing.
            agent_ran = attempt.invoked and attempt.agent_ran
            resolved_now = (succeeded or agent_ran) and not session.unresolved_paths
            round_reason = (
                None if resolved_now else _round_termination_reason(session, invoked=attempt.invoked)
            )
            # Carry it on the session: the exhaustion line, the durable
            # record and every caller downstream read it from there, and
            # a round that computed a reason only for its own console
            # line left them all saying RESOLUTION_CHAIN_EXHAUSTED.
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
            )
            _emit_attempt_outcome(display, outcome)
            route = route_after_round(
                agent_ran=succeeded or agent_ran,
                surviving_marker_paths=session.unresolved_paths,
                round_index=round_index,
                cap=round_cap,
            )
            if route == TERMINAL_RESOLVED:
                _emit_success(display, round_index, stop)
                return True
            if not attempt.invoked:
                # Carry the reason OUT of the round: the exhaustion line
                # and ResolutionOutcome.reason are what the operator and
                # auto-integrate actually read, and "the chain was
                # exhausted" does not explain a chain nobody could spend.
                session.terminal_reason = ResolutionTerminationReason.TOOL_SURFACE_DEAD
                emit_conflict_phase_line(
                    display,
                    "every resolution candidate is a dead tool surface; "
                    "no further round can spend one",
                )
                break
    finally:
        if prompt_path is not None:
            with contextlib.suppress(OSError):
                prompt_path.unlink()
    unresolved = session.unresolved_paths or conflicted
    session.exhaustion_reason = _resolution_exhaustion_reason(session, unresolved)
    emit_conflict_phase_line(
        display,
        "abandoning conflict resolution; "
        + session.exhaustion_reason,
    )
    return False


def _round_termination_reason(
    session: ResolutionSession, *, invoked: bool
) -> ResolutionTerminationReason:
    """Name what ended the round without inventing a verdict nobody gave.

    No reason here is the agent's opinion of the conflict -- it has no
    way to give one. A round that invoked nobody, one whose candidate
    never started, and one whose candidate ran without finishing are
    three different facts, and all three used to be reported as the
    resolver declining the work.
    """
    if session.terminal_reason is not None:
        return session.terminal_reason
    if not invoked:
        return ResolutionTerminationReason.TOOL_SURFACE_DEAD
    # The invocation came back successful and the markers are still
    # there: the resolver worked and did not finish. That is unfinished
    # work, not an answer, and the next round hands it back the paths
    # that still carry markers.
    return ResolutionTerminationReason.RESOLUTION_INCOMPLETE


def _resolution_exhaustion_reason(
    session: ResolutionSession,
    unresolved_paths: tuple[str, ...],
) -> str:
    """Build durable terminal evidence when the resolver cannot finish."""
    reason = (
        session.terminal_reason.value
        if session.terminal_reason is not None
        else "RESOLUTION_CHAIN_EXHAUSTED"
    )
    paths = ", ".join(unresolved_paths) or "<unreadable>"
    return f"{reason}: conflict markers survive in: {paths}"


def _operator_cap_expired(session: ResolutionSession, clock: MonotonicClock) -> bool:
    """Prevent a zero-second cap from reaching an invocation watchdog."""
    cap = session.total_resolution_cap_seconds
    return (
        cap is not None
        and session.started_at is not None
        and clock() - session.started_at >= cap
    )


def _emit_expired_operator_cap(
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
    _emit_attempt_outcome(
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


def _push_round_status(
    display: ParallelDisplay | None,
    root: Path,
    target: str,
    round_index: int,
    round_cap: int,
    stop: RebaseStop | None,
) -> None:
    push_conflict_status_bar(
        display,
        root,
        target=target,
        round_index=round_index,
        round_cap=round_cap,
        stop_index=stop.stop_index if stop is not None else None,
        stop_cap=stop.stop_cap if stop is not None else None,
        replay_index=stop.replay_index if stop is not None else None,
        replay_total=stop.replay_total if stop is not None else None,
        run_started_monotonic=_display_run_started_monotonic(display),
    )


def _emit_success(display: ParallelDisplay | None, round_index: int, stop: RebaseStop | None) -> None:
    next_action = "verifying and continuing the rebase" if stop is not None else "verifying and committing the merge"
    emit_conflict_phase_line(display, f"conflicts resolved in round {round_index}; {next_action}")


def _emit_attempt_outcome(display: ParallelDisplay | None, outcome: ResolutionOutcome) -> None:
    """Render a typed non-success outcome without misclassifying it as a hang."""
    if outcome.succeeded or outcome.reason is None:
        return
    emit_conflict_phase_line(
        display,
        f"{outcome.reason.value}: duration={outcome.duration_seconds:.1f}s; "
        f"last_activity_kind={outcome.last_activity_kind or 'none'}; "
        f"last_activity_at={outcome.last_activity_at or 'never'}; "
        f"last_progress=unresolved_count={len(outcome.unresolved_paths)}; "
        f"unresolved_paths={', '.join(outcome.unresolved_paths)}; "
        f"next=inspect typed reason, keep landed rebase stops, and retry only if identity changed",
    )


def _display_run_started_monotonic(display: ParallelDisplay | None) -> float | None:
    if display is None:
        return None
    try:
        value: object = display.run_started_monotonic
    except AttributeError:
        return None
    return value if isinstance(value, float) else None


def _run_one_round(
    runner: ResolutionInvoker,
    candidates: tuple[str, ...],
    prompt_path: Path,
    round_index: int,
    display: ParallelDisplay | None,
    session: ResolutionSession,
    policy_bundle: PolicyBundle,
) -> RoundAttempt:
    """Spend every live candidate once, starting where the chain left off.

    ``chain_cursor`` is where the NEXT candidate starts, so walking
    FORWARD from it and stopping at the end of the tuple could leave the
    round with nobody to invoke -- either because the cursor had passed
    the last candidate, or because everything from the cursor on was a
    dead tool surface while a live candidate sat behind it. Both cases
    used to end the round with no invocation at all, which the driver
    then reported as a decline no agent ever made. The traversal is
    therefore circular: it visits each candidate at most once per round,
    from the cursor, wrapping past the end. Only a chain with no live
    candidate left returns without invoking.

    ``terminal_reason`` is cleared before each candidate so the fault
    filed against an agent is one it actually produced. Without that,
    the infrastructure reason left by a BROKEN candidate is still set
    when the NEXT candidate fails ordinarily, and that healthy agent
    gets recorded as a dead tool surface for the rest of the rebase.
    The round's reason is therefore the LAST attempt's own; an earlier
    candidate's infrastructure fault is not lost, it is recorded where
    it belongs -- in ``dead_tool_surfaces`` and the operator log.
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
            "every candidate is barred by an earlier infrastructure fault; "
            "retrying them rather than leaving the conflict unresolved",
        )
        session.stop_dead_surfaces = ()
        skipped = _skipped_candidates(session, candidates)
    offset = session.chain_cursor % total
    # Visited positions, not a countdown: the recovery controller owns
    # the cursor and may move it BACKWARDS, and a countdown then spent
    # the round's steps revisiting one candidate while another was never
    # offered the conflict at all.
    visited: set[int] = set()
    # A candidate is re-invoked only while RecoveryController holds the
    # cursor on it, which its own retry budget ends. The explicit bound
    # is here because the alternative to a wrong number is a hung run.
    attempt_cap = max(1, conflict_chain_max_retries(policy_bundle))
    attempts_here = 0
    invoked = False
    agent_ran = False
    while len(visited) < total:
        agent_name = candidates[offset]
        if agent_name in skipped:
            visited.add(offset)
            offset = _next_unvisited(offset, total, visited)
            session.chain_cursor = offset
            continue
        emit_conflict_phase_line(
            display, f"round {round_index}: invoking {agent_name} to resolve the conflicts"
        )
        invoked = True
        session.terminal_reason = None
        # Both describe THIS attempt. Carrying either across candidates
        # let one bad candidate answer for the next one: a healthy agent
        # that really ran and really failed was reported under the fault
        # of the candidate before it, and an unchargeable attempt made
        # the whole stop unchargeable -- which discarded the exhaustion
        # evidence the run needs to escalate honestly.
        session.charge_conflict_budget = True
        try:
            if runner(agent_name, prompt_path, round_index):
                session.chain_cursor = (offset + 1) % total
                return RoundAttempt(succeeded=True, invoked=True, agent_ran=True)
        except Exception as exc:
            logger.warning(
                "conflict_resolution: round {} with '{}' raised: {}", round_index, agent_name, exc
            )
            session.terminal_reason = ResolutionTerminationReason.EXCEPTION
            classify_failed_resolution_attempt(
                session,
                agent_name,
                exc,
                candidates=candidates,
                failed_index=offset,
                policy_bundle=policy_bundle,
            )
        else:
            # A false return is a failed candidate attempt, never evidence
            # that resolution completed. Preserve a typed reason so the
            # terminal outcome can be persisted as exhaustion if every
            # configured fallback also fails.
            #
            # WHICH typed reason depends on evidence the invocation seam
            # left behind: an attempt that ran and came back unsuccessful
            # FAILED, while one that ended on its own limits -- pi out of
            # context, a provider it cannot reach -- EXITED. Neither is a
            # verdict on the conflict; the agent has no way to give one.
            evidence = session.last_attempt_evidence
            if session.terminal_reason is None:
                session.terminal_reason = (
                    ResolutionTerminationReason.CANDIDATE_EXITED
                    if evidence
                    else ResolutionTerminationReason.ATTEMPT_FAILED
                )
            classify_failed_resolution_attempt(
                session,
                agent_name,
                session.last_attempt_failure or evidence or ATTEMPT_FAILED_EVIDENCE,
                candidates=candidates,
                failed_index=offset,
                policy_bundle=policy_bundle,
            )
        agent_ran = agent_ran or session.last_attempt_saw_activity
        if session.terminal_reason in INFRASTRUCTURE_TERMINATION_REASONS:
            _remember_dead_surface(session, agent_name)
            skipped = _skipped_candidates(session, candidates)
            session.charge_conflict_budget = False
            visited.add(offset)
            offset = _next_unvisited(offset, total, visited)
            session.chain_cursor = offset
            continue
        attempts_here += 1
        # The cursor is compared UNWRAPPED: the controller parks it on
        # ``failed_index`` to ask for another go at the same candidate,
        # and only that is a retry. A cursor of ``len(candidates)`` means
        # the chain ran off the end, which is the next candidate's turn.
        if session.chain_cursor == offset and attempts_here < attempt_cap:
            _sleep_conflict_retry(session, policy_bundle)
            continue
        visited.add(offset)
        proposed = session.chain_cursor % total
        offset = proposed if proposed not in visited else _next_unvisited(offset, total, visited)
        session.chain_cursor = offset
        attempts_here = 0
    return RoundAttempt(succeeded=False, invoked=invoked, agent_ran=agent_ran)


def _skipped_candidates(
    session: ResolutionSession, candidates: tuple[str, ...]
) -> frozenset[str]:
    """Candidates this round must not launch, for either reason."""
    barred = (*session.dead_tool_surfaces, *session.stop_dead_surfaces)
    return frozenset(name for name in candidates if name in barred)


def _remember_dead_surface(session: ResolutionSession, agent_name: str) -> None:
    """Bar a candidate, for the run or only for this stop.

    A name the registry cannot produce will not appear mid-run, so that
    bar holds. A tool surface that faulted is Ralph's own plumbing --
    the recovery layer calls the very same failure retryable -- so
    barring the agent for the whole rebase turned one transport hiccup
    into a run that could never resolve anything again, which is exactly
    what the shipped one-agent chain does when its only agent is barred.
    """
    if session.terminal_reason is ResolutionTerminationReason.CANDIDATE_UNAVAILABLE:
        if agent_name not in session.dead_tool_surfaces:
            session.dead_tool_surfaces = (*session.dead_tool_surfaces, agent_name)
        return
    if agent_name not in session.stop_dead_surfaces:
        session.stop_dead_surfaces = (*session.stop_dead_surfaces, agent_name)


def _next_unvisited(offset: int, total: int, visited: set[int]) -> int:
    """Return the next chain position this round has not tried yet."""
    for step in range(1, total + 1):
        candidate = (offset + step) % total
        if candidate not in visited:
            return candidate
    return offset


def _sleep_conflict_retry(session: ResolutionSession, policy_bundle: PolicyBundle) -> None:
    """Honor RecoveryController backoff, falling back to the chain's retry_delay_ms."""
    delay_ms = session.last_retry_delay_ms
    if delay_ms <= 0:
        drain = policy_bundle.agents.agent_drains.get(PHASE_RESOLUTION)
        chain = (
            policy_bundle.agents.agent_chains.get(drain.chain)
            if drain is not None
            else None
        )
        if chain is not None:
            delay_ms = chain.retry_delay_ms
    if delay_ms > 0:
        _sleep_seconds(delay_ms / 1000.0)


def _default_invoker(
    *,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps,
    workspace_scope: WorkspaceScope,
    policy_bundle: PolicyBundle,
    display: ParallelDisplay | None,
    display_context: DisplayContext | None,
    limits: ConflictResolutionConfig,
    round_cap: int,
    inactivity_timeout: float,
    clock: MonotonicClock,
    session: ResolutionSession,
    stop: RebaseStop | None,
    root: Path,
    target: str,
    require_completion_evidence: bool = False,
) -> ResolutionInvoker:
    """Build a conflict-only invocation that shares session cap and status context."""
    cap = session.total_resolution_cap_seconds
    interval = limits.status_interval_seconds

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        # The cap is also checked between rounds, but a round spends
        # every live candidate, so it can expire inside one. A zero
        # remaining cap is rejected by the watchdog's timeout policy,
        # and that ValueError would be filed as a launch EXCEPTION --
        # naming the wrong terminal reason for the operator's own cap.
        remaining = _remaining_operator_cap(cap, session, clock)
        if remaining is not None and remaining <= 0.0:
            session.terminal_reason = ResolutionTerminationReason.OPERATOR_CAP_REACHED
            emit_conflict_phase_line(
                display,
                f"OPERATOR_CAP_REACHED: the resolution cap expired before "
                f"round {round_index} could launch {agent_name}",
            )
            return False
        reporter = ResolutionStatusReporter(
            display=display,
            target=target,
            round_index=round_index,
            round_cap=round_cap,
            stop_index=stop.stop_index if stop is not None else None,
            stop_cap=stop.stop_cap if stop is not None else None,
            clock=clock,
            interval_seconds=interval or 30.0,
            started_at=session.started_at or clock(),
            unresolved_paths=session.unresolved_paths,
            agent_name=agent_name,
        )
        return invoke_resolution_agent(
            agent_name=agent_name,
            prompt_path=prompt_path,
            config=config,
            pipeline_deps=pipeline_deps,
            workspace_scope=workspace_scope,
            policy_bundle=policy_bundle,
            display=display,
            display_context=display_context,
            operator_cap_seconds=remaining,
            inactivity_timeout_seconds=inactivity_timeout,
            status_interval_seconds=interval,
            activity_status_listener=reporter.observe,
            unresolved_paths=session.unresolved_paths,
            session=session,
            require_completion_evidence=require_completion_evidence,
        )

    return _invoke


def _remaining_operator_cap(
    cap: float | None,
    session: ResolutionSession,
    clock: MonotonicClock,
) -> float | None:
    if cap is None or session.started_at is None:
        return None
    return max(0.0, cap - (clock() - session.started_at))
