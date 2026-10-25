"""Round-execution machinery of the conflict-resolution driver.

The per-round candidate-spending loop (:func:`_run_one_round`), the
per-attempt bookkeeping helpers, and the on-disk unresolved-path scan
live in this private companion module so
:mod:`ralph.pipeline.conflict_resolution.driver` stays under the
repo-structure file-size cap; the driver keeps the session lifecycle,
the round loop orchestration, and the public entry points, and
re-exports every name defined here unchanged.

Names that tests monkeypatch on the driver module (``_sleep_seconds``,
``emit_conflict_phase_line``, ``paths_with_conflict_markers``,
``classify_failed_resolution_attempt``) are reached through a deferred
``driver`` module attribute access, exactly like the late binding
:mod:`ralph.pipeline.conflict_resolution.invoker` already uses, so the
monkeypatch surface is unchanged by the split.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

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
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
from ralph.pipeline.conflict_resolution.round_verdict import (
    is_effectively_empty as _is_effectively_empty,
)
from ralph.pipeline.conflict_resolution.round_verdict import (
    round_termination_reason as _round_termination_reason,
)
from ralph.pipeline.conflict_resolution.session import ATTEMPT_FAILED_EVIDENCE
from ralph.pipeline.integration_actions import _DEFAULT_CLOCK, current_clock
from ralph.pipeline.rebase_state import ResolutionFailureCause

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.conflict_resolution.driver import ResolutionInvoker
    from ralph.pipeline.conflict_resolution.session import ResolutionSession
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
    "_reset_conflict_success",
    "_run_one_round",
    "_sleep_conflict_retry",
]


@dataclass(frozen=True)
class RoundAttempt:
    """Record success and whether a resolution candidate was invoked."""

    succeeded: bool
    invoked: bool
    agent_ran: bool = False
    agent_name: str | None = None
    candidate_offset: int = 0


def _evaluate_round_attempt(
    attempt: RoundAttempt,
    session: ResolutionSession,
    display: ParallelDisplay | None,
    round_index: int,
    round_cap: int,
    attempt_started: float,
    clock: Callable[[], float],
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
    # Late binding through the driver namespace: tests monkeypatch
    # ``driver.paths_with_conflict_markers`` there.
    from ralph.pipeline.conflict_resolution import driver as _driver_mod

    marked = list(_driver_mod.paths_with_conflict_markers(root, conflicted))
    decisions = set(decision_paths)
    # A path both sides wrote content into, now empty, is not a
    # resolution: a truncating resolver dropped BOTH sides, and with the
    # markers gone the scan had nothing left to object to.
    emptied = (
        path
        for path in conflicted
        if path in substantive and path not in marked and _is_effectively_empty(root / path)
    )
    vanished = (
        path
        for path in conflicted
        if path in present_before
        and path not in decisions
        and path not in marked
        and not (root / path).exists()
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
    *,
    workspace_changed: bool = False,
) -> None:
    # Late binding through the driver namespace: tests monkeypatch
    # ``driver.classify_failed_resolution_attempt`` there.
    from ralph.pipeline.conflict_resolution import driver as _driver_mod

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
    _driver_mod.classify_failed_resolution_attempt(
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
        workspace_changed=workspace_changed,
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
    # Late binding through the driver namespace: tests monkeypatch
    # ``driver.classify_failed_resolution_attempt`` there.
    from ralph.pipeline.conflict_resolution import driver as _driver_mod

    session.terminal_reason = None
    session.charge_conflict_budget = True
    invoker_result: ResolutionOutcome | bool = False
    probe = session.workspace_probe
    workspace = session.workspace
    if probe is not None and workspace is not None:
        probe.observe_before(PHASE_RESOLUTION, workspace)
    try:
        invoker_result = runner(agent_name, prompt_path, round_index)
        succeeded = (
            invoker_result.succeeded
            if isinstance(invoker_result, ResolutionOutcome)
            else bool(invoker_result)
        )
        if succeeded:
            session.chain_cursor = 0
            session.last_failure_cause = ResolutionFailureCause.NONE
            return True, invoker_result
        if isinstance(invoker_result, ResolutionOutcome):
            _record_invoker_outcome(session, invoker_result)
    except Exception as exc:
        logger.warning(
            "conflict_resolution: round {} with '{}' raised: {}", round_index, agent_name, exc
        )
        session.terminal_reason = ResolutionTerminationReason.EXCEPTION
        session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
        _driver_mod.classify_failed_resolution_attempt(
            session,
            agent_name,
            exc,
            candidates=candidates,
            failed_index=offset,
            policy_bundle=policy_bundle,
            bench=True,
            workspace_changed=bool(
                probe and workspace and probe.has_changed(PHASE_RESOLUTION, workspace)
            ),
        )
        return False, False
    else:
        _handle_candidate_failure(
            session,
            agent_name,
            invoker_result,
            candidates,
            offset,
            policy_bundle,
            workspace_changed=bool(
                probe and workspace and probe.has_changed(PHASE_RESOLUTION, workspace)
            ),
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
        != ResolutionAction.ACCEPT_WORKTREE_REPAIR
    ):
        return False
    # Late binding through the driver namespace: tests monkeypatch
    # ``driver.emit_conflict_phase_line`` there.
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
) -> RoundAttempt:
    """Spend eligible candidates in priority order, respecting bounded retries.

    A successful candidate does not advance priority. Cooldowns survive
    rounds and stops; the controller selects again before every invocation.
    ``worktree_resolved`` rechecks repairs after an unsuccessful attempt.

    Clear terminal evidence before each candidate to preserve attribution.
    """
    # Late binding through the driver namespace: tests monkeypatch
    # ``driver.emit_conflict_phase_line`` there.
    from ralph.pipeline.conflict_resolution import driver as _driver_mod

    total = len(candidates)
    if total == 0:
        return RoundAttempt(succeeded=False, invoked=False)
    skipped = _skipped_candidates(session, candidates)
    if len(skipped) == total and len(session.dead_tool_surfaces) < total:
        # Every remaining candidate is barred by a fault of RALPH's, not
        # by anything about the agents. Doing nothing is the one outcome
        # that cannot resolve the conflict, so the stop-scoped bar is
        # dropped and the chain is spent again rather than skipped.
        _driver_mod.emit_conflict_phase_line(
            display,
            "every candidate is barred by an earlier infrastructure fault; retrying "
            + ", ".join(session.stop_dead_surfaces)
            + " rather than leaving the conflict unresolved",
        )
        session.stop_dead_surfaces = ()
        skipped = _skipped_candidates(session, candidates)
    visited: set[int] = set()
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
            return RoundAttempt(
                succeeded=True,
                invoked=True,
                agent_ran=True,
                agent_name=agent_name,
                candidate_offset=offset,
            )
        agent_saw = session.last_attempt_saw_activity or (
            isinstance(invoker_result, ResolutionOutcome)
            and (
                invoker_result.last_activity_kind is not None or invoker_result.duration_seconds > 0
            )
        )
        agent_ran = agent_ran or agent_saw
        if _check_worktree_repair(
            session, agent_name, round_index, round_cap, display, worktree_resolved, agent_saw
        ):
            _reset_conflict_success(session, agent_name)
            return RoundAttempt(
                succeeded=False,
                invoked=True,
                agent_ran=True,
                agent_name=agent_name,
                candidate_offset=offset,
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
        if session.chain_cursor == offset:
            _sleep_conflict_retry(session, policy_bundle)
            continue
        visited.add(offset)
        proposed = session.chain_cursor % total
        offset = proposed if proposed not in visited else _next_unvisited(offset, total, visited)
        session.chain_cursor = offset
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
    if delay > 0:
        # Wait out the earliest cooldown ONCE, then check controller again.
        # Late binding through the driver namespace: tests monkeypatch
        # ``driver._sleep_seconds`` there.
        from ralph.pipeline.conflict_resolution import driver as _driver_mod

        _driver_mod._sleep_seconds(delay / 1000.0)
        selection = controller.preferred_agent_index(PHASE_RESOLUTION, eligible)
        if selection.agent is not None:
            return candidates.index(selection.agent)
        if current_clock() is _DEFAULT_CLOCK:

            def remaining_wait_ms(name: str) -> int:
                return controller.earliest_available_wait_ms(PHASE_RESOLUTION, [name])

            return candidates.index(min(eligible, key=remaining_wait_ms))
    return None


def _reset_conflict_success(session: ResolutionSession, agent: str) -> None:
    """Reset only the successful resolver's cooldown history."""
    if session.recovery_controller is not None:
        from ralph.pipeline.conflict_resolution.resolver_health import clear_resolver

        clear_resolver(session.recovery_controller, agent)


def _sleep_conflict_retry(session: ResolutionSession, policy_bundle: PolicyBundle) -> None:
    """Honor RecoveryController backoff, falling back to the chain's retry_delay_ms."""
    delay_ms = session.last_retry_delay_ms
    if delay_ms <= 0:
        drain = policy_bundle.agents.agent_drains.get(PHASE_RESOLUTION)
        chain = policy_bundle.agents.agent_chains.get(drain.chain) if drain else None
        delay_ms = chain.retry_delay_ms if chain else 0
    if delay_ms > 0:
        # Late binding through the driver namespace: tests monkeypatch
        # ``driver._sleep_seconds`` there.
        from ralph.pipeline.conflict_resolution import driver as _driver_mod

        _driver_mod._sleep_seconds(delay_ms / 1000.0)
