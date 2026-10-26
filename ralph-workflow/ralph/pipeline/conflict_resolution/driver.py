"""Conflict-resolution driver governed by liveness rather than elapsed time.

The round-execution machinery (candidate spending, per-attempt outcome
recording, the on-disk unresolved-path scan) lives in the private
companion module :mod:`ralph.pipeline.conflict_resolution._driver_rounds`
so this module stays under the repo-structure file-size cap; the session
lifecycle, the round-loop orchestration, and the public entry points
stay here and re-export every moved name unchanged.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import (
    paths_with_conflict_markers,
    unmerged_paths,
)
from ralph.pipeline._retained_resolution_scope import retained_merge_paths
from ralph.pipeline.conflict_resolution._driver_events import (
    announce_declared_decisions as _announce_declared_decisions,
)
from ralph.pipeline.conflict_resolution._driver_events import (
    emit_expired_operator_cap as _emit_expired_operator_cap,
)
from ralph.pipeline.conflict_resolution._driver_events import (
    emit_success as _emit_success,
)
from ralph.pipeline.conflict_resolution._driver_events import (
    render_round_prompt as _render_round_prompt,
)
from ralph.pipeline.conflict_resolution._driver_rounds import (
    RoundAttempt,
    _evaluate_round_attempt,
    _paths_still_unresolved,
    _reset_conflict_success,
    _run_one_round,
)
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.chain_candidates import (
    skipped_candidates,
)
from ralph.pipeline.conflict_resolution.graph import (
    PHASE_RESOLUTION,
)
from ralph.pipeline.conflict_resolution.invoker import default_invoker as _default_invoker
from ralph.pipeline.conflict_resolution.operator_cap import (
    MonotonicClock,
)
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
from ralph.pipeline.conflict_resolution.session import (
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

__all__ = [
    "MonotonicClock",
    "ResolutionAction",
    "ResolutionInvoker",
    "RoundAttempt",
    "_sleep_seconds",
    "classify_failed_resolution_attempt",
    "conflict_chain_max_retries",
    "emit_conflict_phase_line",
    "invoke_resolution_agent",
    "paths_with_conflict_markers",
    "render_conflict_prompt",
    "run_conflict_resolution_outcome",
    "run_conflict_resolution_pipeline",
    "run_rebase_conflict_resolution_outcome",
    "run_rebase_conflict_resolution_pipeline",
    "select_resolution_action",
]


def _sleep_seconds(
    seconds: float,
    *,
    sleep: Callable[[float], None] | None = None,
) -> None:
    """Wait through the injected clock seam (the real clock in production)."""
    (sleep if sleep is not None else current_clock().sleep)(seconds)


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
    ensure_session_controller(session, policy_bundle, pipeline_deps=pipeline_deps)
    if session.workspace is None and workspace_scope is not None:
        ws_obj: object = getattr(workspace_scope, "workspace", None)
        from ralph.workspace.protocol import Workspace

        if isinstance(ws_obj, Workspace):
            session.workspace = ws_obj
        else:
            from ralph.workspace.fs import FsWorkspace

            session.workspace = FsWorkspace(
                workspace_scope.root,
                allowed_roots=workspace_scope.allowed_roots,
            )
    if session.workspace_probe is None:
        rc = session.recovery_controller
        if rc is not None and rc.workspace_probe is not None:
            session.workspace_probe = rc.workspace_probe
        else:
            from ralph.workspace.probe import DefaultWorkspaceChangeProbe

            session.workspace_probe = DefaultWorkspaceChangeProbe()
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
        root,
        target,
        runner,
        candidates,
        session,
        display,
        conflicted,
        decision_paths,
        present_at_entry,
        substantive_at_entry,
        round_cap,
        clock,
        stop,
        strategy_history,
        policy_bundle,
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
        round_index = session.rounds_spent
        while True:
            round_index += 1
            if _operator_cap_expired(session, clock):
                _emit_expired_operator_cap(display, session, conflicted, clock)
                return False
            _push_round_status(
                display, root, target, round_index, max(round_index, round_cap), stop
            )
            prompt_path = _render_round_prompt(
                root,
                target,
                conflicted,
                round_index,
                max(round_index, round_cap),
                session,
                stop,
                strategy_history,
            )
            if prompt_path is None:
                session.terminal_reason = ResolutionTerminationReason.PROMPT_UNAVAILABLE
                session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
                session.exhaustion_reason = _resolution_exhaustion_reason(session, conflicted)
                emit_conflict_phase_line(display, "could not materialize the resolution prompt")
                return False
            attempt_started = clock()
            _reset_round_reporting(session)

            def _unresolved() -> tuple[str, ...]:
                return _paths_still_unresolved(
                    root,
                    conflicted,
                    decision_paths,
                    present_before=present_at_entry,
                    substantive=substantive_at_entry,
                )

            attempt = _run_one_round(
                runner,
                candidates,
                prompt_path,
                round_index,
                display,
                session,
                policy_bundle,
                worktree_resolved=lambda: not decision_paths and not _unresolved(),
            )
            session.unresolved_paths = _unresolved()
            if attempt.succeeded and session.unresolved_paths and attempt.agent_name is not None:
                logger.warning(
                    "conflict_resolution: round {} with '{}' reported success, but unresolved paths remain in worktree",
                    round_index,
                    attempt.agent_name,
                )
                session.last_attempt_failure = (
                    "unresolved paths remain in worktree after candidate reported success"
                )
                session.last_attempt_evidence = (
                    "unresolved paths remain in worktree after candidate reported success"
                )
                session.terminal_reason = ResolutionTerminationReason.RESOLUTION_INCOMPLETE
                session.last_failure_cause = ResolutionFailureCause.UNRESOLVED_CONFLICT
                classify_failed_resolution_attempt(
                    session,
                    attempt.agent_name,
                    session.last_attempt_failure,
                    candidates=candidates,
                    failed_index=attempt.candidate_offset,
                    policy_bundle=policy_bundle,
                    workspace_changed=bool(
                        session.workspace_probe
                        and session.workspace
                        and session.workspace_probe.has_changed(PHASE_RESOLUTION, session.workspace)
                    ),
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
                if attempt.agent_name is not None:
                    _reset_conflict_success(session, attempt.agent_name)
                return True
            if not attempt.invoked:
                _handle_dead_candidate_chain(session, conflicted, display, candidates=candidates)
                break
            session.rounds_spent = round_index
            if action == ResolutionAction.ESCALATE_TO_INTERVENTION:
                _escalate_unreachable_remainder(session, display)
                break
            if not _can_continue_rounds(session, candidates):
                break
    finally:
        if prompt_path is not None:
            with contextlib.suppress(OSError):
                prompt_path.unlink()
    return False


def _can_continue_rounds(session: ResolutionSession, candidates: tuple[str, ...]) -> bool:
    skipped = skipped_candidates(session, candidates)
    live = [name for name in candidates if name not in skipped]
    if not live:
        return False
    controller = session.recovery_controller
    if controller is not None:
        if any(controller._is_agent_available(PHASE_RESOLUTION, name) for name in live):
            return True
        return controller.earliest_available_wait_ms(PHASE_RESOLUTION, live) > 0
    if session.ledger is not None:
        return any(session.ledger.is_available(PHASE_RESOLUTION, name) for name in live)
    return True


def _record_unconfigured_resolver(
    session: ResolutionSession,
    conflicted: tuple[str, ...],
    display: ParallelDisplay | None,
) -> None:
    session.terminal_reason = ResolutionTerminationReason.NO_RESOLVER_CONFIGURED
    session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
    session.unresolved_paths = conflicted
    session.exhaustion_reason = _resolution_exhaustion_reason(session, conflicted)
    emit_conflict_phase_line(display, "no agent bound to the rebase-conflict-resolution drain")


def _handle_dead_candidate_chain(
    session: ResolutionSession,
    conflicted: tuple[str, ...],
    display: ParallelDisplay | None,
    candidates: Sequence[str] = (),
) -> None:
    from ralph.recovery.controller import RecoveryController

    controller = session.recovery_controller
    if session.terminal_reason is None:
        if isinstance(controller, RecoveryController) and candidates:
            delay = controller.earliest_available_wait_ms(PHASE_RESOLUTION, list(candidates))
            if delay > 0:
                session.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
            else:
                session.terminal_reason = ResolutionTerminationReason.TOOL_SURFACE_DEAD
        else:
            session.terminal_reason = ResolutionTerminationReason.TOOL_SURFACE_DEAD
    if session.last_failure_cause == ResolutionFailureCause.NONE:
        session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
    session.unresolved_paths = session.unresolved_paths or conflicted
    if session.terminal_reason == ResolutionTerminationReason.CANDIDATE_UNAVAILABLE:
        emit_conflict_phase_line(
            display,
            "all resolution candidates are unavailable or on cooldown; conflict resolution remains pending",
        )
    else:
        emit_conflict_phase_line(
            display,
            "every resolution candidate is a dead tool surface; no further round can spend one",
        )


def _escalate_unreachable_remainder(
    session: ResolutionSession, display: ParallelDisplay | None
) -> bool:
    if not session.out_of_reach_paths:
        return False
    session.terminal_reason = ResolutionTerminationReason.OUT_OF_REACH
    session.unresolved_paths = session.out_of_reach_paths
    paths = ", ".join(session.out_of_reach_paths)
    emit_conflict_phase_line(
        display,
        f"the resolvable conflicts are repaired, but no resolver can repair {paths}",
    )
    return True


# ----- AC-14 catalog evidence -----
# This file is the authoritative source for the catalog entries listed
# below. Each ``# AC-14 rationale: <ID>`` line is the code-adjacent
# marker the AC-14 audit looks for; each ``# ladder rung: <N>``
# names the rung the entry sits on. Adding a new entry here requires
# BOTH lines or the audit fails.

# AC-14 rationale: F5
# ladder rung: 3
# ----- end AC-14 catalog evidence -----
