"""Backward-compatible boolean projections of the typed resolution outcomes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.conflict_resolution.driver import ResolutionInvoker
    from ralph.pipeline.conflict_resolution.operator_cap import MonotonicClock
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
    from ralph.pipeline.conflict_resolution.session import ResolutionSession
    from ralph.pipeline.factory import PipelineDeps
    from ralph.policy.models import PolicyBundle
    from ralph.workspace.scope import WorkspaceScope


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
    strategy_history: tuple[str, ...] = (),
) -> bool:
    """Backward-compatible boolean projection of the typed merge outcome."""
    from ralph.pipeline.conflict_resolution.driver import run_conflict_resolution_outcome

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
        strategy_history=strategy_history,
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
    strategy_history: tuple[str, ...] = (),
) -> bool:
    """Backward-compatible projection of the out-of-graph ``PHASE_RESOLUTION`` outcome."""
    from ralph.pipeline.conflict_resolution.driver import run_rebase_conflict_resolution_outcome

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
        strategy_history=strategy_history,
    ).succeeded
