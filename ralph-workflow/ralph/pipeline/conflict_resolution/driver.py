from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import (
    conflict_stage_entries,
    paths_with_conflict_markers,
    unmerged_paths,
)
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline._integration_status import IntegrationStatus, build_integration_status
from ralph.pipeline._retained_resolution_scope import retained_merge_paths
from ralph.pipeline.conflict_resolution._driver_events import (
    announce_declared_decisions as _announce_declared_decisions,
)
from ralph.pipeline.conflict_resolution._driver_events import (
    emit_success as _emit_success,
)
from ralph.pipeline.conflict_resolution._driver_rounds import (
    RoundAttempt,
    _evaluate_round_attempt,
    _paths_still_unresolved,
    _run_one_round,
)
from ralph.pipeline.conflict_resolution._operator_command import operator_command_for_paths
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.chain_candidates import (
    skipped_candidates,
)
from ralph.pipeline.conflict_resolution.git_worktree_observer import GitWorktreeConflictObserver
from ralph.pipeline.conflict_resolution.graph import (
    PHASE_RESOLUTION,
)
from ralph.pipeline.conflict_resolution.invoker import default_invoker as _default_invoker
from ralph.pipeline.conflict_resolution.operator_cap import (
    MonotonicClock,
)
from ralph.pipeline.conflict_resolution.pipeline_entrypoints import (
    run_conflict_resolution_pipeline,
    run_rebase_conflict_resolution_pipeline,
)
from ralph.pipeline.conflict_resolution.prompt import (
    PromptMaterializer,
    materialize_round_prompt,
    render_conflict_prompt,
)
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
    out_of_reach_escalation_message,
    out_of_reach_paths,
    out_of_reach_reasons,
    stage_mechanical_conflicts,
)
from ralph.pipeline.conflict_resolution.status import (
    capture_status_bar_model,
)
from ralph.pipeline.conflict_resolution.status import (
    emit_conflict_phase_line as _status_emit_conflict_phase_line,
)
from ralph.pipeline.integration_actions import current_clock
from ralph.pipeline.resolution_outcome import StopResolutionOutcome
from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome

_ACTIVE_SESSION: list[ResolutionSession | None] = [None]


def _reset_conflict_success(session: ResolutionSession, agent: str) -> None:
    if session.recovery_controller is not None:
        from ralph.pipeline.conflict_resolution.resolver_health import clear_resolver

        clear_resolver(session.recovery_controller, agent)


def emit_conflict_phase_line(
    display: object, message: str, *, channel: str = "rebase-conflict"
) -> None:
    if message.endswith(": next strategy"):
        reason_label = message.removesuffix(": next strategy")
        session = _ACTIVE_SESSION[0]
        if session is not None and (
            session.last_activity_kind is not None
            or session.last_activity_at is not None
            or (session.last_duration_seconds is not None and session.last_duration_seconds > 0)
        ):
            dur = (
                session.last_duration_seconds
                if session.last_duration_seconds is not None
                else 0.0
            )
            act_kind = session.last_activity_kind or "none"
            act_at = session.last_activity_at or "never"
            paths = ", ".join(session.unresolved_paths)
            unres_count = len(session.unresolved_paths)
            message = (
                f"{reason_label}: duration={dur:.1f}s; "
                f"last_activity_kind={act_kind}; "
                f"last_activity_at={act_at}; "
                f"last_progress=unresolved_count={unres_count}; "
                f"unresolved_paths={paths}; "
                f"next=inspect typed reason, keep landed rebase stops, and retry only if identity changed"
            )
    _status_emit_conflict_phase_line(display, message, channel=channel)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
    from ralph.pipeline.conflict_resolution.worktree_observer import WorktreeConflictObserver
    from ralph.pipeline.factory import PipelineDeps
    from ralph.policy.models import PolicyBundle
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger
    from ralph.recovery.controller import RecoveryController
    from ralph.workspace.scope import WorkspaceScope


type ResolutionInvoker = Callable[
    [str, "Path", int], ResolutionOutcome | StopResolutionOutcome | bool
]

_QUERY_FAILED_SENTINEL = "<unmerged-path-query-failed>"

_CHAIN_STATE_TERMINATION_REASONS: frozenset[ResolutionTerminationReason] = frozenset(
    {
        ResolutionTerminationReason.TOOL_SURFACE_DEAD,
        ResolutionTerminationReason.CANDIDATE_UNAVAILABLE,
        ResolutionTerminationReason.CANDIDATE_EXHAUSTED,
    }
)

__all__ = [
    "MonotonicClock",
    "ResolutionAction",
    "ResolutionInvoker",
    "RoundAttempt",
    "_sleep_seconds",
    "classify_failed_resolution_attempt",
    "classify_unmerged_conflicts",
    "conflict_chain_max_retries",
    "declared_decision_paths",
    "emit_conflict_phase_line",
    "invoke_resolution_agent",
    "out_of_reach_escalation_message",
    "out_of_reach_reasons",
    "paths_with_conflict_markers",
    "render_conflict_prompt",
    "retained_merge_paths",
    "run_conflict_resolution_outcome",
    "run_conflict_resolution_pipeline",
    "run_rebase_conflict_resolution_outcome",
    "run_rebase_conflict_resolution_pipeline",
    "select_resolution_action",
    "stage_mechanical_conflicts",
    "unmerged_paths",
]


def _sleep_seconds(
    seconds: float,
    *,
    sleep: Callable[[float], None] | None = None,
    session: ResolutionSession | None = None,
) -> None:
    del session
    (sleep if sleep is not None else current_clock().sleep)(seconds)


def run_conflict_resolution_outcome(
    *,
    root: Path,
    target: str,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps,
    workspace_scope: WorkspaceScope | None = None,
    policy_bundle: PolicyBundle,
    display: ParallelDisplay | None,
    display_context: DisplayContext | None,
    invoke: ResolutionInvoker | None = None,
    clock: MonotonicClock | None = None,
    session: ResolutionSession | None = None,
    strategy_history: tuple[str, ...] = (),
    observer: WorktreeConflictObserver | None = None,
    prompt_materializer: PromptMaterializer | None = None,
) -> StopResolutionOutcome:
    active_session = session or _new_resolution_session(config)
    previous_model = capture_status_bar_model(display)
    clock_candidate: object = (
        getattr(active_session.ledger, "_clock", None)  # type: ignore[misc]
        or getattr(active_session.ledger, "clock", None)  # type: ignore[misc]
        if active_session.ledger is not None
        else None
    )
    if clock_candidate is None and pipeline_deps is not None:
        clock_candidate = pipeline_deps.recovery_clock
    token = None
    if clock_candidate is not None and hasattr(clock_candidate, "sleep"):
        from ralph.pipeline.integration_actions import install_executors

        token = install_executors(clock=clock_candidate)  # type: ignore[arg-type]
    _ACTIVE_SESSION[0] = active_session
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
            observer=observer,
            prompt_materializer=prompt_materializer,
        )
        return _resolution_outcome(active_session, resolved)
    except Exception as exc:
        logger.warning("conflict_resolution: pipeline failed: {}", exc)
        emit_conflict_phase_line(display, f"conflict resolution failed: {exc}")
        active_session.terminal_reason = ResolutionTerminationReason.EXCEPTION
        active_session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
        return _resolution_outcome(active_session, False)
    finally:
        _ACTIVE_SESSION[0] = None
        if token is not None:
            from ralph.pipeline.integration_actions import reset_executors

            reset_executors(token)
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
    observer: WorktreeConflictObserver | None = None,
    prompt_materializer: PromptMaterializer | None = None,
) -> StopResolutionOutcome:
    active_session = (
        session or active_rebase_resolution_session() or _new_resolution_session(config)
    )
    _ACTIVE_SESSION[0] = active_session
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
            observer=observer,
            prompt_materializer=prompt_materializer,
        )
        return _resolution_outcome(active_session, resolved)
    except Exception as exc:
        logger.warning("conflict_resolution: rebase stop {} failed: {}", stop.stop_index, exc)
        emit_conflict_phase_line(display, f"rebase conflict resolution failed: {exc}")
        active_session.terminal_reason = ResolutionTerminationReason.EXCEPTION
        active_session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
        return _resolution_outcome(active_session, False)
    finally:
        _ACTIVE_SESSION[0] = None


def _new_resolution_session(config: UnifiedConfig) -> ResolutionSession:
    limits = config.conflict_resolution
    return ResolutionSession(
        inactivity_timeout_seconds=limits.inactivity_timeout_seconds,
        max_rounds_per_stop=limits.max_rounds_per_stop,
        max_rebase_conflict_stops=limits.max_rebase_conflict_stops,
        max_fallback_agents=limits.max_fallback_agents,
        gitlink_policy=limits.gitlink_policy,
    )


def _prepare_conflicted_paths(
    root: Path,
    stop: RebaseStop | None,
    session: ResolutionSession,
    display: ParallelDisplay | None,
    observer: WorktreeConflictObserver,
) -> tuple[tuple[str, ...], bool | None, tuple[str, ...], IntegrationStatus | None]:
    conflicted = (
        stop.conflicted_files
        if stop is not None
        else observer.retained_merge_paths(root, tuple(observer.unmerged_paths(root)))
    )
    if _QUERY_FAILED_SENTINEL in conflicted:
        session.terminal_reason = ResolutionTerminationReason.OUT_OF_REACH
        session.last_failure_cause = ResolutionFailureCause.CONTEXT_FAILURE
        session.unresolved_paths = tuple(conflicted)
        session.exhaustion_reason = "OUT_OF_REACH: the conflicted paths could not be read"
        emit_conflict_phase_line(
            display, "conflicted paths unreadable; nothing a resolver can repair"
        )
        return (), False, (), None
    if not conflicted:
        emit_conflict_phase_line(display, "no conflicted paths; nothing for a resolver to repair")
        return (), False, (), None
    session.out_of_reach_paths = ()
    kinds = observer.classify_unmerged_conflicts(root, conflicted)
    unreachable = out_of_reach_paths(kinds)
    reasons: dict[str, str] = (
        observer.out_of_reach_reasons(kinds)
        if hasattr(observer, "out_of_reach_reasons")
        else out_of_reach_reasons(kinds)
    )
    reachable = tuple(path for path in conflicted if path not in set(unreachable))
    if unreachable and not reachable:
        session.terminal_reason = ResolutionTerminationReason.OUT_OF_REACH
        session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
        session.unresolved_paths = unreachable
        msg = (
            session.operator_action_message
            if session.operator_action_message
            and "needs operator action:" in session.operator_action_message
            else (
                session.exhaustion_reason
                if session.exhaustion_reason
                and "needs operator action:" in session.exhaustion_reason
                else out_of_reach_escalation_message(unreachable, reasons)
            )
        )
        session.exhaustion_reason = msg
        session.operator_action_message = msg
        emit_conflict_phase_line(
            display,
            "OUT_OF_REACH: escalating on sight without spending the chain; " + msg,
        )
        return (), False, (), None
    integration_status: IntegrationStatus | None = None
    if unreachable:
        session.out_of_reach_paths = unreachable
        msg = out_of_reach_escalation_message(unreachable, reasons)
        session.operator_action_message = msg
        session.exhaustion_reason = msg
        integration_status = build_integration_status(
            cause=ResolutionTerminationReason.OUT_OF_REACH.value,
            paths_with_reasons=tuple(
                (path, reasons.get(path, "gitlink conflict")) for path in unreachable
            ),
            command=operator_command_for_paths(unreachable),
            agents_can_finish=False,
        )
        emit_conflict_phase_line(
            display,
            "OUT_OF_REACH: no resolver can repair "
            + ", ".join(unreachable)
            + "; resolving the rest first",
        )
        conflicted = reachable
    decisions = tuple(
        path for path in observer.declared_decision_paths(kinds) if path in set(conflicted)
    )
    staged = observer.stage_mechanical_conflicts(root, kinds)
    remaining = tuple(path for path in conflicted if path not in set(staged))
    if remaining:
        return remaining, None, decisions, integration_status
    if session.out_of_reach_paths:
        _escalate_unreachable_remainder(session, display)
        return (), False, (), integration_status
    emit_conflict_phase_line(
        display, "mechanical conflicts staged without spending the resolution chain"
    )
    return (), True, (), integration_status


def _declared_decision_status(root: Path, paths: tuple[str, ...]) -> IntegrationStatus | None:
    if not paths:
        return None
    try:
        entries = conflict_stage_entries(root, paths)
    except Exception:
        facts = tuple((path, "marker-less declaration") for path in paths)
    else:
        facts = tuple(
            (
                path,
                _declared_decision_reason(root, path, entries[path])
                if path in entries
                else "marker-less declaration",
            )
            for path in paths
        )
    return build_integration_status(
        cause="DECLARED_DECISION",
        paths_with_reasons=facts,
        command="",
        agents_can_finish=True,
    )


def _declared_decision_reason(
    root: Path,
    path: str,
    stages: dict[int, tuple[str, str]],
) -> str:
    base = stages.get(1)
    ours = stages.get(2)
    theirs = stages.get(3)
    if ours is None or theirs is None:
        return "delete/modify declaration"
    ours_mode, ours_blob = ours
    theirs_mode, theirs_blob = theirs
    if ours_mode != theirs_mode:
        return "mode change declaration"
    try:
        binary = b"\0" in (root / path).read_bytes()
    except OSError:
        binary = False
    if binary:
        return "binary declaration"
    if base is None:
        return "rename declaration"
    if ours_blob != theirs_blob:
        return "marker-less declaration"
    return "marker-less declaration"


def _status_scope(status: IntegrationStatus) -> str:
    details = "; ".join(f"{path}: {reason}" for path, reason in status.paths_with_reasons)
    return f"{status.summary}; path reasons: {details}" if details else status.summary


def _wrap_invoker(
    base_runner: ResolutionInvoker,
    session: ResolutionSession,
) -> ResolutionInvoker:
    def runner(
        agent_name: str, prompt_path: Path, round_index: int
    ) -> StopResolutionOutcome | bool:
        raw = base_runner(agent_name, prompt_path, round_index)
        if isinstance(raw, ResolutionOutcome):
            if raw.failure_cause is not ResolutionFailureCause.NONE:
                session.last_failure_cause = raw.failure_cause
            if isinstance(raw.reason, ResolutionTerminationReason):
                session.terminal_reason = raw.reason
            return raw.to_stop_resolution_outcome()
        return raw

    return runner


def _run_rounds(
    *,
    root: Path,
    target: str,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps,
    workspace_scope: WorkspaceScope | None,
    policy_bundle: PolicyBundle,
    display: ParallelDisplay | None,
    display_context: DisplayContext | None,
    invoke: ResolutionInvoker | None,
    clock: MonotonicClock,
    stop: RebaseStop | None,
    session: ResolutionSession,
    strategy_history: tuple[str, ...],
    observer: WorktreeConflictObserver | None,
    prompt_materializer: PromptMaterializer | None,
) -> bool:
    from ralph.workspace.scope import WorkspaceScope

    effective_scope = workspace_scope if workspace_scope is not None else WorkspaceScope(root)
    active_observer = observer if observer is not None else GitWorktreeConflictObserver()
    materialize_prompt = (
        prompt_materializer if prompt_materializer is not None else materialize_round_prompt
    )
    limits = config.conflict_resolution
    round_cap = session.max_rounds_per_stop or limits.max_rounds_per_stop
    inactivity_timeout = session.inactivity_timeout_seconds or limits.inactivity_timeout_seconds
    is_rotation = bool(strategy_history)
    begin_resolution_stop(session, is_strategy_rotation=is_rotation)
    if pipeline_deps is not None and session.unavailability_store is None:
        store: object = getattr(pipeline_deps, "unavailability_store", None)
        from ralph.recovery.agent_unavailability_tracker import UnavailabilityStore

        if isinstance(store, UnavailabilityStore):
            session.unavailability_store = store
    ensure_session_controller(session, policy_bundle, pipeline_deps=pipeline_deps, config=config)
    if session.workspace is None:
        ws_obj: object = getattr(effective_scope, "workspace", None)
        from ralph.workspace.protocol import Workspace

        if isinstance(ws_obj, Workspace):
            session.workspace = ws_obj
        else:
            ws_root: object = getattr(effective_scope, "root", None)
            from pathlib import Path

            if isinstance(ws_root, Path):
                from ralph.workspace.fs import FsWorkspace

                allowed_obj: object = getattr(effective_scope, "allowed_roots", ())
                allowed = allowed_obj if isinstance(allowed_obj, tuple) else ()
                session.workspace = FsWorkspace(
                    ws_root,
                    allowed_roots=allowed,
                )
    if session.workspace_probe is None:
        rc = session.recovery_controller
        if rc is not None and rc.workspace_probe is not None:
            session.workspace_probe = rc.workspace_probe
        else:
            from ralph.workspace.probe import DefaultWorkspaceChangeProbe

            session.workspace_probe = DefaultWorkspaceChangeProbe()
    prepared, early, decision_paths, integration_status = _prepare_conflicted_paths(
        root, stop, session, display, active_observer
    )
    if early is not None:
        return early
    conflicted = prepared
    _announce_declared_decisions(display, _declared_decision_status(root, decision_paths))
    candidates = resolution_chain_agents(policy_bundle)
    if not candidates:
        _record_unconfigured_resolver(session, conflicted, display)
        return False
    if session.started_at is None:
        session.started_at = clock()
    session.unresolved_paths = conflicted
    base_runner = invoke or _default_invoker(
        config=config,
        pipeline_deps=pipeline_deps,
        workspace_scope=effective_scope,
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
    runner = _wrap_invoker(base_runner, session)
    entry_scope = (
        f"{len(conflicted)} resolvable; {_status_scope(integration_status)}"
        if integration_status is not None
        else f"{len(conflicted)} conflicted file(s)"
    )
    emit_conflict_phase_line(
        display,
        f"entering rebase conflict resolution for '{target}' ({entry_scope})"
        + (f" replaying {stop.sha[:8]} {stop.subject}" if stop is not None else ""),
    )
    present_at_entry = frozenset(
        path for path in conflicted if active_observer.path_exists(root / path)
    )
    substantive_at_entry = frozenset(
        path for path in present_at_entry if not active_observer.is_effectively_empty(root / path)
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
        active_observer,
        materialize_prompt,
    )
    if resolved:
        return True
    unresolved = session.unresolved_paths
    session.exhaustion_reason = _resolution_exhaustion_reason(session, unresolved)
    emit_conflict_phase_line(
        display,
        "abandoning conflict resolution; " + session.exhaustion_reason,
    )
    return False


def _handle_repaired_or_fast_forward(
    action: ResolutionAction,
    attempt: RoundAttempt,
    session: ResolutionSession,
    display: ParallelDisplay | None,
    round_index: int,
    stop: RebaseStop | None,
) -> bool:
    if action == ResolutionAction.ACCEPT_WORKTREE_REPAIR and not (
        attempt.succeeded
        or session.last_attempt_saw_activity
        or session.exhaustion_reason is not None
    ):
        session.terminal_reason = ResolutionTerminationReason.ATTEMPT_FAILED
        return False
    if _escalate_unreachable_remainder(session, display):
        return False
    _emit_success(display, round_index, stop)
    if attempt.agent_name is not None:
        _reset_conflict_success(session, attempt.agent_name)
    return True


def _classify_incomplete_success(
    attempt: RoundAttempt,
    session: ResolutionSession,
    round_index: int,
    candidates: Sequence[str],
    policy_bundle: PolicyBundle,
) -> None:
    if not (attempt.succeeded and session.unresolved_paths and attempt.agent_name is not None):
        return
    logger.warning(
        "conflict_resolution: round {} with '{}' reported success, but unresolved paths remain in worktree",
        round_index,
        attempt.agent_name,
    )
    unresolved_count = len(session.unresolved_paths)
    made_progress = attempt.probe_changed or attempt.count_dropped
    session.terminal_reason = ResolutionTerminationReason.RESOLUTION_INCOMPLETE
    session.last_failure_cause = ResolutionFailureCause.UNRESOLVED_CONFLICT
    classify_failed_resolution_attempt(
        session,
        attempt.agent_name,
        None,
        candidates=candidates,
        failed_index=attempt.candidate_offset,
        policy_bundle=policy_bundle,
        outcome=(
            ConflictAttemptOutcome.UNRESOLVED_REMAINING
            if made_progress
            else ConflictAttemptOutcome.STALLED
        ),
        unresolved_count=unresolved_count,
        workspace_changed=made_progress,
    )


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
    observer: WorktreeConflictObserver,
    prompt_materializer: PromptMaterializer,
) -> bool:
    prompt_path: Path | None = None
    try:
        round_index = session.rounds_spent
        last_invoked_reason: ResolutionTerminationReason | None = session.terminal_reason
        last_invoked_charge: bool = session.charge_conflict_budget
        while round_index < round_cap:
            round_index += 1
            _push_round_status(display, root, target, round_index, round_cap, stop)
            prompt_path = prompt_materializer(
                root=root,
                target=target,
                conflicted_paths=conflicted,
                round_index=round_index,
                round_cap=round_cap,
                session=session,
                stop=stop,
                strategy_history=strategy_history,
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
                    observer=observer,
                )

            _wait_for_candidates_if_cooling_down(session, candidates)

            attempt = _run_one_round(
                runner,
                candidates,
                prompt_path,
                round_index,
                display,
                session,
                policy_bundle,
                worktree_resolved=lambda: not decision_paths and not session.unresolved_paths,
                observe_unresolved=_unresolved,
            )
            _classify_incomplete_success(attempt, session, round_index, candidates, policy_bundle)
            action = _evaluate_round_attempt(
                attempt,
                session,
                display,
                round_index,
                round_cap,
                attempt_started,
                clock,
                decision_paths,
                candidates=candidates,
            )
            if action in (
                ResolutionAction.PROCEED_TO_FAST_FORWARD,
                ResolutionAction.ACCEPT_WORKTREE_REPAIR,
            ):
                if _handle_repaired_or_fast_forward(
                    action, attempt, session, display, round_index, stop
                ):
                    return True
                break
            if not attempt.invoked:
                if session.rounds_spent > 0 and last_invoked_reason is not None:
                    session.terminal_reason = last_invoked_reason
                    session.charge_conflict_budget = last_invoked_charge
                else:
                    _handle_dead_candidate_chain(
                        session, conflicted, display, candidates=candidates
                    )
                break
            session.rounds_spent = round_index
            last_invoked_reason = session.terminal_reason
            last_invoked_charge = session.charge_conflict_budget
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


def _wait_for_candidates_if_cooling_down(
    session: ResolutionSession, candidates: Sequence[str]
) -> None:
    controller = session.recovery_controller
    if controller is not None:
        skipped = skipped_candidates(session, tuple(candidates))
        live = [name for name in candidates if name not in skipped]
        if live and not any(
            controller.agent_eligibility(PHASE_RESOLUTION, name).is_eligible for name in live
        ):
            wait_ms = controller.earliest_available_wait_ms(PHASE_RESOLUTION, live)
            if wait_ms > 0:
                _sleep_seconds(wait_ms / 1000.0)
                if not any(
                    controller.agent_eligibility(PHASE_RESOLUTION, name).is_eligible
                    for name in live
                ):
                    for name in live:
                        controller.unavailability_store.reset_backoff(PHASE_RESOLUTION, name)

def _can_continue_rounds(session: ResolutionSession, candidates: tuple[str, ...]) -> bool:
    skipped = skipped_candidates(session, candidates)
    live = [name for name in candidates if name not in skipped]
    if not live:
        return False
    controller = session.recovery_controller
    if controller is not None:
        if controller.all_exhausted(PHASE_RESOLUTION, live):
            return False
        return (
            any(controller.agent_eligibility(PHASE_RESOLUTION, name).is_eligible for name in live)
            or controller.earliest_available_wait_ms(PHASE_RESOLUTION, live) > 0
        )
    if session.ledger is not None:
        if session.ledger.any_eligible(PHASE_RESOLUTION, live):
            return True
        wait = session.ledger.earliest_wait_ms(PHASE_RESOLUTION, live)
        return wait is not None and wait > 0
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


def _dead_chain_reason(
    live_candidates: Sequence[str],
    controller: RecoveryController | None,
    ledger: AgentFailureLedger | None = None,
) -> ResolutionTerminationReason:
    del ledger
    wait: int | None = None
    if controller is not None:
        wait = controller.earliest_eligible_wait_ms(PHASE_RESOLUTION, list(live_candidates))
    if wait is None:
        return ResolutionTerminationReason.CANDIDATE_EXHAUSTED
    if wait > 0:
        return ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
    return ResolutionTerminationReason.CANDIDATE_EXHAUSTED


def _agent_unavailability_reason(
    agent: str,
    ledger: AgentFailureLedger | None,
    controller: RecoveryController | None,
) -> str:
    del ledger
    if controller is not None:
        snap = controller.unavailability_store.snapshot()
        u_reasons = snap.get("unavailability_reasons", {})
        if isinstance(u_reasons, dict) and agent in u_reasons:
            return str(u_reasons[agent])
        reason = controller.ledger.failure_reason(PHASE_RESOLUTION, agent)
        if reason:
            return reason
    return "spent"


def _handle_dead_candidate_chain(
    session: ResolutionSession,
    conflicted: tuple[str, ...],
    display: ParallelDisplay | None,
    candidates: Sequence[str] = (),
) -> None:
    from ralph.pipeline.conflict_resolution._operator_command import operator_command_for_paths

    controller = session.recovery_controller
    ledger = session.ledger
    paths = tuple(session.unresolved_paths or conflicted)
    session.unresolved_paths = paths

    barred = skipped_candidates(session, tuple(candidates))
    live_candidates = [agent for agent in candidates if agent not in barred]

    if session.rounds_spent == 0 and (
        session.terminal_reason is None
        or session.terminal_reason in _CHAIN_STATE_TERMINATION_REASONS
    ):
        session.terminal_reason = _dead_chain_reason(
            live_candidates,
            controller,
            ledger,
        )
    elif session.terminal_reason is None:
        session.terminal_reason = ResolutionTerminationReason.ATTEMPT_FAILED

    if session.last_failure_cause == ResolutionFailureCause.NONE:
        session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE

    if session.terminal_reason == ResolutionTerminationReason.CANDIDATE_UNAVAILABLE:
        emit_conflict_phase_line(
            display,
            "all resolution candidates are unavailable or on cooldown; conflict resolution remains pending",
        )
    elif session.terminal_reason == ResolutionTerminationReason.CANDIDATE_EXHAUSTED:
        reasons_list = [
            f"{agent}: {_agent_unavailability_reason(agent, ledger, controller)}"
            for agent in candidates
        ]
        reasons_str = ", ".join(reasons_list)
        paths_str = ", ".join(paths)
        cmd = operator_command_for_paths(paths)
        msg = (
            f"{ResolutionTerminationReason.CANDIDATE_EXHAUSTED.value}: "
            f"all {len(candidates)} resolver agents are spent/unavailable ({reasons_str}); "
            f"conflict on {paths_str} needs operator action: {cmd}"
        )
        session.operator_action_message = msg
        emit_conflict_phase_line(display, msg)
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
    msg = (
        session.operator_action_message
        if session.operator_action_message
        and "needs operator action:" in session.operator_action_message
        else (
            session.exhaustion_reason
            if session.exhaustion_reason and "needs operator action:" in session.exhaustion_reason
            else out_of_reach_escalation_message(session.out_of_reach_paths)
        )
    )
    session.exhaustion_reason = msg
    session.operator_action_message = msg
    emit_conflict_phase_line(
        display,
        f"the resolvable conflicts are repaired, but no resolver can repair {paths}",
    )
    return True
