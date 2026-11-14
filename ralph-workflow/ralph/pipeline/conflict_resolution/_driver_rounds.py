from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.conflict_resolution._driver_events import (
    emit_attempt_outcome as _emit_attempt_outcome,
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
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.resolution_action import (
    ResolutionAction,
    select_resolution_action,
)
from ralph.pipeline.conflict_resolution.round_verdict import (
    round_termination_reason as _round_termination_reason,
)
from ralph.pipeline.resolution_outcome import (
    ConfigFailureOutcome,
    NextStrategyOutcome,
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
    UnknownOutcome,
    WaitingOutcome,
    assert_never,
)
from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.conflict_resolution.driver import ResolutionInvoker
    from ralph.pipeline.conflict_resolution.session import ResolutionSession
    from ralph.pipeline.conflict_resolution.worktree_observer import WorktreeConflictObserver
    from ralph.pipeline.resolution_outcome import StopResolutionOutcome
    from ralph.policy.models import PolicyBundle

__all__ = [
    "RoundAttempt",
    "_check_worktree_repair",
    "_evaluate_round_attempt",
    "_execute_single_candidate",
    "_handle_candidate_failure",
    "_paths_still_unresolved",
    "_preferred_conflict_candidate",
    "_record_invoker_outcome",
    "_run_one_round",
    "_sleep_conflict_retry",
]


@dataclass(frozen=True)
class RoundAttempt:
    succeeded: bool
    invoked: bool
    agent_ran: bool = False
    agent_name: str | None = None
    candidate_offset: int = 0
    count_dropped: bool = False
    probe_changed: bool = False


def _evaluate_round_attempt(
    attempt: RoundAttempt,
    session: ResolutionSession,
    display: ParallelDisplay | None,
    round_index: int,
    round_cap: int,
    attempt_started: float,
    clock: Callable[[], float],
    decision_paths: tuple[str, ...],
    candidates: Sequence[str] = (),
) -> ResolutionAction:
    succeeded = attempt.succeeded
    agent_ran = attempt.invoked and attempt.agent_ran
    proved_by_worktree = agent_ran and not decision_paths
    resolved_now = (succeeded or proved_by_worktree) and not session.unresolved_paths
    action = select_resolution_action(session, round_index + 1, round_cap, resolved=resolved_now)
    round_reason = (
        None
        if resolved_now
        else _round_termination_reason(session, invoked=attempt.invoked, candidates=candidates)
    )
    session.terminal_reason = round_reason
    if resolved_now:
        outcome: StopResolutionOutcome = ResolvedOutcome()
    else:
        outcome = NextStrategyOutcome(
            strategy=round_reason.value if round_reason is not None else "resolution_incomplete"
        )
    _emit_attempt_outcome(display, outcome, session=session)
    return action


def _paths_still_unresolved(
    root: Path,
    conflicted: tuple[str, ...],
    decision_paths: tuple[str, ...],
    *,
    present_before: frozenset[str],
    substantive: frozenset[str],
    observer: WorktreeConflictObserver,
) -> tuple[str, ...]:
    marked = list(observer.paths_with_conflict_markers(root, conflicted))
    decisions = set(decision_paths)
    emptied = (
        path
        for path in conflicted
        if path in substantive and path not in marked and observer.is_effectively_empty(root / path)
    )
    vanished = (
        path
        for path in conflicted
        if path in present_before
        and path not in decisions
        and path not in marked
        and not observer.path_exists(root / path)
    )
    seen: set[str] = set()
    unresolved: list[str] = []
    for path in (*marked, *emptied, *vanished):
        if path not in seen:
            seen.add(path)
            unresolved.append(path)
    return tuple(unresolved)


def _record_invoker_outcome(
    session: ResolutionSession,
    invoker_result: StopResolutionOutcome,
) -> None:
    match invoker_result:
        case ResolvedOutcome():
            session.last_failure_cause = ResolutionFailureCause.NONE
        case WaitingOutcome(external_condition=condition):
            session.exhaustion_reason = condition
        case RetryLaterOutcome(on_change=on_change, until=until):
            session.exhaustion_reason = on_change or f"retry at {until}"
        case NextStrategyOutcome(strategy=strategy):
            session.exhaustion_reason = strategy
        case ConfigFailureOutcome(parse_error=parse_error):
            session.last_failure_cause = ResolutionFailureCause.CONFIG_FAILURE
            session.exhaustion_reason = str(parse_error) if parse_error is not None else None
        case UnknownOutcome(error=error):
            session.exhaustion_reason = str(error)
        case _ as unreachable:
            assert_never(unreachable)


def _handle_candidate_failure(
    session: ResolutionSession,
    agent_name: str,
    invoker_result: object,
    candidates: tuple[str, ...],
    offset: int,
    policy_bundle: PolicyBundle,
    *,
    workspace_changed: bool = False,
) -> None:
    from ralph.pipeline.conflict_resolution import driver as _driver_mod

    fault = classify_attempt_fault(session.terminal_reason)
    if session.terminal_reason is None:
        session.terminal_reason = (
            attempt_fault_to_termination_reason(fault) or ResolutionTerminationReason.ATTEMPT_FAILED
        )
    raw_failure: BaseException | str | None
    if isinstance(session.last_attempt_failure, BaseException):
        outcome = ConflictAttemptOutcome.RAW
        raw_failure = session.last_attempt_failure
    else:
        outcome = session.last_attempt_outcome or ConflictAttemptOutcome.ATTEMPT_FAILED
        raw_failure = (
            session.last_attempt_failure if isinstance(session.last_attempt_failure, str) else None
        )
    _driver_mod.classify_failed_resolution_attempt(
        session,
        agent_name,
        raw_failure,
        candidates=candidates,
        failed_index=offset,
        policy_bundle=policy_bundle,
        bench=(
            session.terminal_reason in RESOLVER_NOT_SPENT_TERMINATION_REASONS
            and session.terminal_reason != ResolutionTerminationReason.ATTEMPT_FAILED
        ),
        workspace_changed=workspace_changed,
        outcome=outcome,
    )
    if session.terminal_reason in RESOLVER_NOT_SPENT_TERMINATION_REASONS:
        session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
    else:
        session.last_failure_cause = ResolutionFailureCause.UNRESOLVED_CONFLICT


def _execute_single_candidate(
    runner: ResolutionInvoker,
    agent_name: str,
    prompt_path: Path,
    round_index: int,
    session: ResolutionSession,
    candidates: tuple[str, ...],
    offset: int,
    policy_bundle: PolicyBundle,
    observe_unresolved: Callable[[], tuple[str, ...]] | None = None,
) -> tuple[bool, StopResolutionOutcome, RoundAttempt]:
    session.terminal_reason = None
    session.last_attempt_failure = None
    session.last_attempt_result = None
    session.last_attempt_outcome = None
    session.charge_conflict_budget = True
    invoker_result: StopResolutionOutcome = RetryLaterOutcome(on_change="resolution attempt failed")
    probe = session.workspace_probe
    workspace = session.workspace
    if probe is not None and workspace is not None:
        probe.observe_before(PHASE_RESOLUTION, workspace)
    unresolved_before = len(session.unresolved_paths)
    raised: BaseException | None = None
    succeeded = False
    try:
        raw_result = runner(agent_name, prompt_path, round_index)
        if isinstance(raw_result, bool):
            invoker_result = (
                ResolvedOutcome()
                if raw_result
                else RetryLaterOutcome(
                    on_change="conflict resolution incomplete; progress retained for next agent"
                )
            )
        else:
            invoker_result = raw_result
        succeeded = isinstance(invoker_result, ResolvedOutcome)
        if succeeded:
            session.chain_cursor = 0
            session.last_failure_cause = ResolutionFailureCause.NONE
        else:
            _record_invoker_outcome(session, invoker_result)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning(
            "conflict_resolution: round {} with '{}' raised: {}", round_index, agent_name, exc
        )
        session.terminal_reason = ResolutionTerminationReason.EXCEPTION
        session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
        session.last_attempt_failure = exc
        raised = exc
    count_dropped = False
    if observe_unresolved is not None:
        unresolved_after = observe_unresolved()
        session.unresolved_paths = unresolved_after
        count_dropped = len(unresolved_after) < unresolved_before
    probe_changed = bool(probe and workspace and probe.has_changed(PHASE_RESOLUTION, workspace))
    progress = RoundAttempt(
        succeeded=False,
        invoked=True,
        agent_ran=True,
        agent_name=agent_name,
        candidate_offset=offset,
        count_dropped=count_dropped,
        probe_changed=probe_changed,
    )
    if raised is not None:
        _handle_candidate_failure(
            session,
            agent_name,
            UnknownOutcome(raised),
            candidates,
            offset,
            policy_bundle,
            workspace_changed=probe_changed or count_dropped,
        )
        return False, UnknownOutcome(raised), progress
    if succeeded:
        return True, invoker_result, progress
    _handle_candidate_failure(
        session,
        agent_name,
        invoker_result,
        candidates,
        offset,
        policy_bundle,
        workspace_changed=probe_changed or count_dropped,
    )
    return False, invoker_result, progress


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
        != ResolutionAction.ACCEPT_WORKTREE_REPAIR
    ):
        return False
    from ralph.pipeline.conflict_resolution import driver as _driver_mod

    _driver_mod.emit_conflict_phase_line(
        display,
        f"round {round_index}: {agent_name} ended badly but the worktree shows "
        "every marker repaired; not spending another candidate",
    )
    return True


def _run_one_round(
    runner: ResolutionInvoker,
    candidates: tuple[str, ...],
    prompt_path: Path,
    round_index: int,
    display: ParallelDisplay | None,
    session: ResolutionSession,
    policy_bundle: PolicyBundle,
    worktree_resolved: Callable[[], bool] | None = None,
    observe_unresolved: Callable[[], tuple[str, ...]] | None = None,
) -> RoundAttempt:
    from ralph.pipeline.conflict_resolution import driver as _driver_mod

    total = len(candidates)
    if total == 0:
        return RoundAttempt(succeeded=False, invoked=False)
    skipped = _skipped_candidates(session, candidates)
    visited: set[int] = set()
    candidate_retries: dict[int, int] = {}
    from ralph.pipeline.conflict_resolution.session import conflict_chain_max_retries

    max_retries = conflict_chain_max_retries(policy_bundle)
    invoked = False
    agent_ran = False
    round_cap = session.max_rounds_per_stop or 3
    while len(visited) < total:
        selected = _preferred_conflict_candidate(session, candidates, visited, skipped)
        if selected is None:
            return RoundAttempt(succeeded=False, invoked=invoked, agent_ran=agent_ran)
        offset = selected
        agent_name = candidates[offset]
        _driver_mod.emit_conflict_phase_line(
            display, f"round {round_index}: invoking {agent_name} to resolve the conflicts"
        )
        invoked = True
        succeeded, _invoker_result, progress = _execute_single_candidate(
            runner,
            agent_name,
            prompt_path,
            round_index,
            session,
            candidates,
            offset,
            policy_bundle,
            observe_unresolved,
        )
        if succeeded:
            return RoundAttempt(
                succeeded=True,
                invoked=True,
                agent_ran=True,
                agent_name=agent_name,
                candidate_offset=offset,
                count_dropped=progress.count_dropped,
                probe_changed=progress.probe_changed,
            )
        agent_saw = bool(session.last_attempt_saw_activity)
        agent_ran = agent_ran or agent_saw
        if _check_worktree_repair(
            session, agent_name, round_index, round_cap, display, worktree_resolved, agent_saw
        ):
            return RoundAttempt(
                succeeded=False,
                invoked=True,
                agent_ran=True,
                agent_name=agent_name,
                candidate_offset=offset,
                count_dropped=progress.count_dropped,
                probe_changed=progress.probe_changed,
            )
        if session.terminal_reason in INFRASTRUCTURE_TERMINATION_REASONS:
            _remember_dead_surface(session, agent_name)
            skipped = _skipped_candidates(session, candidates)
            session.charge_conflict_budget = False
            session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
            visited.add(offset)
            offset = _next_unvisited(offset, total, visited)
            session.chain_cursor = offset
            continue
        made_progress = progress.count_dropped or progress.probe_changed
        if made_progress:
            candidate_retries[offset] = 0
        retries = candidate_retries.get(offset, 0)
        retries_same = session.chain_cursor == offset
        if not session.skip_same_agent_retries and retries_same and retries < max_retries:
            candidate_retries[offset] = 0 if made_progress else (retries + 1)
            _sleep_conflict_retry(session, policy_bundle)
            continue
        visited.add(offset)
        proposed = _next_unvisited(offset, total, visited)
        offset = proposed
        session.chain_cursor = offset
    return RoundAttempt(succeeded=False, invoked=invoked, agent_ran=agent_ran)


def _preferred_conflict_candidate(
    session: ResolutionSession,
    candidates: tuple[str, ...],
    visited: set[int],
    skipped: frozenset[str],
) -> int | None:
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
        return candidates.index(eligible[0]) if eligible else None
    selection = controller.preferred_agent_index(PHASE_RESOLUTION, eligible)
    if selection.agent is not None:
        return candidates.index(selection.agent)
    delay = controller.earliest_available_wait_ms(PHASE_RESOLUTION, eligible)
    if (
        delay == 0
        and not session.caller_provided
        and hasattr(controller, "ledger")
        and controller.ledger is not None
    ):
        delay = controller.ledger.earliest_wait_ms(PHASE_RESOLUTION, eligible) or 0
    if delay > 0:
        if session.caller_provided:
            return None
        from ralph.pipeline.conflict_resolution import driver as _driver_mod

        mono_before = controller.clock.monotonic() if hasattr(controller, "clock") else 0.0
        _driver_mod._sleep_seconds(delay / 1000.0)
        mono_after = controller.clock.monotonic() if hasattr(controller, "clock") else 0.0
        if mono_after - mono_before < (delay / 1000.0) * 0.5:
            clock_inst = getattr(controller, "clock", getattr(controller, "_clock", None))
            if hasattr(clock_inst, "advance"):
                clock_inst.advance(delay / 1000.0)
            store_clock = getattr(
                controller.unavailability_store, "clock", getattr(controller.unavailability_store, "_clock", None)
            )
            if store_clock is not clock_inst and hasattr(store_clock, "advance"):
                store_clock.advance(delay / 1000.0)
            controller.prune_expired()
        selection = controller.preferred_agent_index(PHASE_RESOLUTION, eligible)
        if selection.agent is not None:
            return candidates.index(selection.agent)

        def _candidate_wait_ms(name: str) -> int:
            wait = controller.earliest_available_wait_ms(PHASE_RESOLUTION, [name])
            if wait == 0 and hasattr(controller, "ledger") and controller.ledger is not None:
                wait = controller.ledger.earliest_wait_ms(PHASE_RESOLUTION, [name]) or 0
            return wait

        shortest_agent = min(
            eligible,
            key=_candidate_wait_ms,
        )
        return candidates.index(shortest_agent)
    return None


def _sleep_conflict_retry(session: ResolutionSession, policy_bundle: PolicyBundle) -> None:
    delay_ms = session.last_retry_delay_ms
    if delay_ms <= 0:
        drain = policy_bundle.agents.agent_drains.get(PHASE_RESOLUTION)
        chain = policy_bundle.agents.agent_chains.get(drain.chain) if drain else None
        delay_ms = chain.retry_delay_ms if chain else 0
    if delay_ms > 0:
        from ralph.pipeline.conflict_resolution import driver as _driver_mod

        _driver_mod._sleep_seconds(delay_ms / 1000.0)
