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
from ralph.pipeline.conflict_resolution.graph import TERMINAL_RESOLVED, route_after_round
from ralph.pipeline.conflict_resolution.prompt import render_conflict_prompt
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
from ralph.pipeline.conflict_resolution.session import (
    ResolutionSession,
    invoke_resolution_agent,
    resolution_chain_agents,
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
            session=ResolutionSession(
                max_rebase_conflict_stops=config.conflict_resolution.max_rebase_conflict_stops
            ),
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
            session=ResolutionSession(
                max_rebase_conflict_stops=config.conflict_resolution.max_rebase_conflict_stops
            ),
        )
    except Exception as exc:
        logger.warning("conflict_resolution: rebase stop {} failed: {}", stop.stop_index, exc)
        emit_conflict_phase_line(display, f"rebase conflict resolution failed: {exc}")
        return False


def _restore_status_bar(display: ParallelDisplay | None, root: Path, previous_model: object | None) -> None:
    if previous_model is None:
        clear_conflict_status_bar(
            display,
            root,
            run_started_monotonic=_display_run_started_monotonic(display),
        )
    else:
        restore_status_bar(display, previous_model)


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
    conflicted = stop.conflicted_files if stop is not None else tuple(unmerged_paths(root))
    if not conflicted or _QUERY_FAILED_SENTINEL in conflicted:
        emit_conflict_phase_line(display, "no readable conflicted paths; nothing a resolver can repair")
        return False
    candidates = resolution_chain_agents(policy_bundle)[: limits.max_fallback_agents]
    if not candidates:
        emit_conflict_phase_line(display, "no agent bound to the rebase-conflict-resolution drain")
        return False
    session.started_at = clock()
    session.unresolved_paths = ()
    runner = invoke or _default_invoker(
        config=config,
        pipeline_deps=pipeline_deps,
        workspace_scope=workspace_scope,
        policy_bundle=policy_bundle,
        display=display,
        display_context=display_context,
        limits=limits,
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
        for round_index in range(1, limits.max_rounds_per_stop + 1):
            _push_round_status(display, root, target, round_index, limits.max_rounds_per_stop, stop)
            prompt_path = render_conflict_prompt(
                root=root,
                target=target,
                conflicted_paths=conflicted,
                round_index=round_index,
                round_cap=limits.max_rounds_per_stop,
                surviving_marker_paths=session.unresolved_paths,
                replaying_commit_sha=stop.sha if stop is not None else None,
                replaying_commit_subject=stop.subject if stop is not None else None,
                stop_index=stop.stop_index if stop is not None else None,
                stop_cap=stop.stop_cap if stop is not None else None,
            )
            if prompt_path is None:
                emit_conflict_phase_line(display, "could not materialize the resolution prompt")
                return False
            attempt_started = clock()
            succeeded = _run_one_round(runner, candidates, prompt_path, round_index, display)
            session.unresolved_paths = tuple(paths_with_conflict_markers(root, conflicted))
            outcome = ResolutionOutcome(
                succeeded=succeeded and not session.unresolved_paths,
                reason=(
                    None
                    if succeeded and not session.unresolved_paths
                    else ResolutionTerminationReason.CANDIDATE_DECLINED
                ),
                duration_seconds=max(0.0, clock() - attempt_started),
                last_activity_kind=None,
                last_activity_at=None,
                unresolved_paths=session.unresolved_paths,
            )
            _emit_attempt_outcome(display, outcome)
            route = route_after_round(
                invocation_succeeded=succeeded,
                surviving_marker_paths=session.unresolved_paths,
                round_index=round_index,
                cap=limits.max_rounds_per_stop,
            )
            if route == TERMINAL_RESOLVED:
                _emit_success(display, round_index, stop)
                return True
    finally:
        if prompt_path is not None:
            with contextlib.suppress(OSError):
                prompt_path.unlink()
    emit_conflict_phase_line(
        display,
        "abandoning conflict resolution; conflict markers survive in: "
        + ", ".join(session.unresolved_paths or conflicted),
    )
    return False


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
        f"unresolved_paths={', '.join(outcome.unresolved_paths)}",
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
) -> bool:
    for agent_name in candidates:
        emit_conflict_phase_line(display, f"round {round_index}: invoking {agent_name} to resolve the conflicts")
        try:
            if runner(agent_name, prompt_path, round_index):
                return True
        except Exception as exc:
            logger.warning("conflict_resolution: round {} with '{}' raised: {}", round_index, agent_name, exc)
    return False


def _default_invoker(
    *,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps,
    workspace_scope: WorkspaceScope,
    policy_bundle: PolicyBundle,
    display: ParallelDisplay | None,
    display_context: DisplayContext | None,
    limits: ConflictResolutionConfig,
    clock: MonotonicClock,
    session: ResolutionSession,
    stop: RebaseStop | None,
    root: Path,
    target: str,
) -> ResolutionInvoker:
    """Build a conflict-only invocation that shares session cap and status context."""
    cap = limits.total_resolution_cap_seconds
    interval = limits.status_interval_seconds

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        reporter = ResolutionStatusReporter(
            display=display,
            target=target,
            round_index=round_index,
            round_cap=limits.max_rounds_per_stop,
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
            status_interval_seconds=interval,
            activity_status_listener=reporter.observe,
            unresolved_paths=session.unresolved_paths,
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
