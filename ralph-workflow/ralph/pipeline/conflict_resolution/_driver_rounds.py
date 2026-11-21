from __future__ import annotations

import subprocess
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from git.exc import GitError
from loguru import logger

from ralph.agents.invoke import AgentInvocationError
from ralph.git.errors import GitOperationError
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.conflict_resolution._driver_events import (
    emit_attempt_outcome as _emit_attempt_outcome,
)
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.attempt_fault import (
    BUDGET_UNCHARGED_TERMINATION_REASONS,
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
from ralph.recovery.attempt_facts import AttemptFacts
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
    "_revive_cooling_candidates",
    "_run_one_round",
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
    from ralph.git.merge import conflict_stage_entries, stage_paths, unmerged_paths
    from ralph.pipeline.conflict_resolution.deterministic_resolution import (
        _pick_and_stage_gitlinks,
    )

    entries = conflict_stage_entries(root, conflicted)
    gitlink_paths = [
        p for p, stg in entries.items() if any(m == "160000" for m, _h in stg.values())
    ]
    if gitlink_paths:
        _pick_and_stage_gitlinks(root, gitlink_paths, None)

    marked = list(observer.paths_with_conflict_markers(root, conflicted))
    decisions = set(decision_paths)
    stageable = [
        path
        for path in conflicted
        if path not in marked
        and observer.path_exists(root / path)
        and not observer.is_effectively_empty(root / path)
    ]
    if stageable:
        stage_paths(root, tuple(stageable))
    unmerged = set(unmerged_paths(root))
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
    for path in (*marked, *emptied, *vanished, *(p for p in conflicted if p in unmerged)):
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
            session.halt_reason = condition
        case RetryLaterOutcome(on_change=on_change, until=until):
            session.halt_reason = on_change or f"retry at {until}"
        case NextStrategyOutcome(strategy=strategy):
            session.halt_reason = strategy
        case ConfigFailureOutcome(parse_error=parse_error):
            session.last_failure_cause = ResolutionFailureCause.CONFIG_FAILURE
            session.halt_reason = str(parse_error) if parse_error is not None else None
        case UnknownOutcome(error=error):
            session.halt_reason = str(error)
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

    del invoker_result
    if isinstance(session.last_attempt_failure, BaseException):
        typed_outcome = ConflictAttemptOutcome.RAW
        exc: BaseException | None = session.last_attempt_failure
    else:
        typed_outcome = session.last_attempt_outcome or ConflictAttemptOutcome.ATTEMPT_FAILED
        exc = None
    _driver_mod.finalize_resolution_attempt_failure(
        session,
        agent_name,
        facts=AttemptFacts(
            artifact_valid=False,
            workspace_changed=workspace_changed,
            unresolved_count=len(session.unresolved_paths),
        ),
        exc=exc,
        outcome=typed_outcome,
        candidates=candidates,
        failed_index=offset,
        policy_bundle=policy_bundle,
    )
    if session.terminal_reason is None:
        session.terminal_reason = ResolutionTerminationReason.ATTEMPT_FAILED
    if session.terminal_reason in BUDGET_UNCHARGED_TERMINATION_REASONS:
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
    except (
        AgentInvocationError,
        GitError,
        GitOperationError,
        subprocess.SubprocessError,
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
    ) as exc:
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
    agent_ran = bool(session.last_attempt_saw_activity) if not succeeded else True
    progress = RoundAttempt(
        succeeded=False,
        invoked=True,
        agent_ran=agent_ran,
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
    round_cap: int,
    worktree_resolved: Callable[[], bool] | None = None,
    observe_unresolved: Callable[[], tuple[str, ...]] | None = None,
    cohort_wait_ms: int = 0,
) -> RoundAttempt:
    from ralph.pipeline.conflict_resolution import driver as _driver_mod

    total = len(candidates)
    if total == 0:
        return RoundAttempt(succeeded=False, invoked=False)
    visited: set[int] = set()
    attempt = RoundAttempt(succeeded=False, invoked=False)
    revived = False
    while len(visited) < total:
        selected = _preferred_conflict_candidate(session, candidates, visited)
        if selected is None:
            if revived or not _revive_cooling_candidates(
                session, candidates, visited, cohort_wait_ms
            ):
                return RoundAttempt(
                    succeeded=False, invoked=attempt.invoked, agent_ran=attempt.agent_ran
                )
            revived = True
            continue
        agent_name = candidates[selected]
        _driver_mod.emit_conflict_phase_line(
            display, f"round {round_index}: invoking {agent_name} to resolve the conflicts"
        )
        succeeded, invoker_result, progress = _execute_single_candidate(
            runner,
            agent_name,
            prompt_path,
            round_index,
            session,
            candidates,
            selected,
            policy_bundle,
            observe_unresolved,
        )
        agent_saw = bool(
            session.last_attempt_saw_activity or not isinstance(invoker_result, ResolvedOutcome)
        )
        if _check_worktree_repair(
            session, agent_name, round_index, round_cap, display, worktree_resolved, agent_saw
        ):
            return RoundAttempt(
                succeeded=False,
                invoked=True,
                agent_ran=True,
                agent_name=agent_name,
                candidate_offset=selected,
                count_dropped=progress.count_dropped,
                probe_changed=progress.probe_changed,
            )
        attempt = RoundAttempt(
            succeeded=succeeded,
            invoked=True,
            agent_ran=succeeded or agent_saw,
            agent_name=agent_name,
            candidate_offset=selected,
            count_dropped=progress.count_dropped,
            probe_changed=progress.probe_changed,
        )
        if succeeded and session.unresolved_paths:
            _driver_mod._classify_incomplete_success(
                attempt, session, round_index, candidates, policy_bundle
            )
        if succeeded and (not session.unresolved_paths or progress.count_dropped):
            return attempt
        if succeeded:
            attempt = replace(attempt, succeeded=False)
        visited.add(selected)
    return attempt


def _revive_cooling_candidates(
    session: ResolutionSession,
    candidates: tuple[str, ...],
    visited: set[int],
    cohort_wait_ms: int,
) -> bool:
    from ralph.pipeline import run_loop
    from ralph.pipeline.cooldown_wait_context import CooldownWaitContext
    from ralph.pipeline.state import PipelineState
    from ralph.recovery.controller import RecoveryController

    controller = session.recovery_controller
    if not isinstance(controller, RecoveryController) or cohort_wait_ms <= 0:
        return False
    pending = [name for index, name in enumerate(candidates) if index not in visited]
    cohort = [
        name
        for name in pending
        if 0 < controller.earliest_available_wait_ms(PHASE_RESOLUTION, [name]) <= cohort_wait_ms
    ]
    if not cohort:
        return False
    wait_ms = controller.earliest_available_wait_ms(PHASE_RESOLUTION, cohort)
    if wait_ms <= 0:
        return False
    state = (
        session.recovery_state
        if isinstance(session.recovery_state, PipelineState)
        else PipelineState(phase=PHASE_RESOLUTION)
    )
    state = state.copy_with(last_retry_delay_ms=wait_ms)
    from ralph.pipeline.conflict_resolution import driver as _driver_mod

    run_loop.handle_cooldown_delay(
        state,
        CooldownWaitContext(sleep=_driver_mod._sleep_seconds, controller=controller),
    )
    controller.prune_expired()
    return any(
        status.is_eligible
        for _agent, status in controller.chain_eligibility(PHASE_RESOLUTION, cohort)
    )


def _preferred_conflict_candidate(
    session: ResolutionSession,
    candidates: tuple[str, ...],
    visited: set[int],
) -> int | None:
    from ralph.pipeline.conflict_resolution.session import ensure_session_controller

    selection = ensure_session_controller(session).preferred_agent_index(
        PHASE_RESOLUTION,
        list(candidates),
        exclude=frozenset(candidates[index] for index in visited),
    )
    if selection.agent is not None:
        return candidates.index(selection.agent)
    return None
