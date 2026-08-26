"""Conflict-resolution driver governed by liveness rather than elapsed time."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
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
    ResolutionSession,
    classify_failed_resolution_attempt,
    invoke_resolution_agent,
    resolution_chain_agents,
)
from ralph.pipeline.conflict_resolution.sight import (
    classify_unmerged_conflicts,
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


def _sleep_seconds(seconds: float) -> None:
    """Sleep without exposing ``time.sleep`` as the patch target for tests."""
    time.sleep(seconds)  # filesystem-poll-ok: RecoveryController chain retry_delay_ms backoff

__all__ = [
    "MonotonicClock",
    "ResolutionInvoker",
    "run_conflict_resolution_pipeline",
    "run_rebase_conflict_resolution_pipeline",
]


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
    """Resolve an in-progress merge through fixed-window liveness supervision."""
    previous_model = capture_status_bar_model(display)
    try:
        return _run_rounds(
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
            session=session or _new_resolution_session(config),
        )
    except Exception as exc:
        logger.warning("conflict_resolution: pipeline failed: {}", exc)
        emit_conflict_phase_line(display, f"conflict resolution failed: {exc}")
        return False
    finally:
        _restore_status_bar(display, root, previous_model)


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
    """Resolve one paused rebase stop with the same fixed liveness window."""
    try:
        return _run_rounds(
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
            session=session or active_rebase_resolution_session() or _new_resolution_session(config),
        )
    except Exception as exc:
        logger.warning("conflict_resolution: rebase stop {} failed: {}", stop.stop_index, exc)
        emit_conflict_phase_line(display, f"rebase conflict resolution failed: {exc}")
        return False


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
) -> tuple[tuple[str, ...], bool | None]:
    """Classify on sight; return remaining paths or an early pipeline verdict."""
    conflicted = stop.conflicted_files if stop is not None else tuple(unmerged_paths(root))
    if not conflicted or _QUERY_FAILED_SENTINEL in conflicted:
        emit_conflict_phase_line(display, "no readable conflicted paths; nothing a resolver can repair")
        return (), False
    kinds = classify_unmerged_conflicts(root, conflicted)
    unreachable = out_of_reach_paths(kinds)
    if unreachable:
        session.terminal_reason = ResolutionTerminationReason.OUT_OF_REACH
        emit_conflict_phase_line(
            display,
            "OUT_OF_REACH: escalating on sight without spending the chain; unresolved_paths="
            + ", ".join(unreachable),
        )
        return (), False
    staged = stage_mechanical_conflicts(root, kinds)
    if not staged:
        return conflicted, None
    remaining = tuple(path for path in conflicted if path not in set(staged))
    if remaining:
        return remaining, None
    emit_conflict_phase_line(
        display, "mechanical conflicts staged without spending the resolution chain"
    )
    return (), True


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
    prepared, early = _prepare_conflicted_paths(root, stop, session, display)
    if early is not None:
        return early
    conflicted = prepared
    candidates = resolution_chain_agents(policy_bundle)
    if not candidates:
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
            succeeded = _run_one_round(
                runner, candidates, prompt_path, round_index, display, session, policy_bundle
            )
            session.unresolved_paths = tuple(paths_with_conflict_markers(root, conflicted))
            outcome = ResolutionOutcome(
                succeeded=succeeded and not session.unresolved_paths,
                reason=(
                    None
                    if succeeded and not session.unresolved_paths
                    else session.terminal_reason or ResolutionTerminationReason.CANDIDATE_DECLINED
                ),
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
                invocation_succeeded=succeeded,
                surviving_marker_paths=session.unresolved_paths,
                round_index=round_index,
                cap=round_cap,
            )
            if route == TERMINAL_RESOLVED:
                _emit_success(display, round_index, stop)
                return True
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
) -> bool:
    offset = min(session.chain_cursor, len(candidates))
    if offset >= len(candidates):
        if len(candidates) != 1:
            return False
        offset = 0
    while offset < len(candidates):
        agent_name = candidates[offset]
        if agent_name in session.dead_tool_surfaces:
            offset += 1
            continue
        emit_conflict_phase_line(
            display, f"round {round_index}: invoking {agent_name} to resolve the conflicts"
        )
        try:
            if runner(agent_name, prompt_path, round_index):
                session.chain_cursor = offset + 1
                return True
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
            classify_failed_resolution_attempt(
                session,
                agent_name,
                "candidate declined",
                candidates=candidates,
                failed_index=offset,
                policy_bundle=policy_bundle,
            )
        if session.terminal_reason in INFRASTRUCTURE_TERMINATION_REASONS:
            remembered = list(session.dead_tool_surfaces)
            if agent_name not in remembered:
                remembered.append(agent_name)
            session.dead_tool_surfaces = tuple(remembered)
            session.charge_conflict_budget = False
            offset = session.chain_cursor if session.chain_cursor > offset else offset + 1
            continue
        if session.chain_cursor == offset:
            _sleep_conflict_retry(session, policy_bundle)
            continue
        offset = session.chain_cursor
    return False


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
) -> ResolutionInvoker:
    """Build a conflict-only invocation that shares session cap and status context."""
    cap = session.total_resolution_cap_seconds
    interval = limits.status_interval_seconds

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
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
            operator_cap_seconds=_remaining_operator_cap(cap, session, clock),
            inactivity_timeout_seconds=inactivity_timeout,
            status_interval_seconds=interval,
            activity_status_listener=reporter.observe,
            unresolved_paths=session.unresolved_paths,
            session=session,
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
