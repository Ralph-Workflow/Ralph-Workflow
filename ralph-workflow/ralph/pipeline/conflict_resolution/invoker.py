from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.conflict_resolution.status import (
    ResolutionStatusReporter,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.conflict_resolution_config import ConflictResolutionConfig
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


def default_invoker(
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
        from ralph.pipeline.conflict_resolution import driver as _driver

        patched = _driver.invoke_resolution_agent
        result = patched(
            agent_name=agent_name,
            prompt_path=prompt_path,
            config=config,
            pipeline_deps=pipeline_deps,
            workspace_scope=workspace_scope,
            policy_bundle=policy_bundle,
            display=display,
            display_context=display_context,
            inactivity_timeout_seconds=inactivity_timeout,
            status_interval_seconds=interval,
            activity_status_listener=reporter.observe,
            unresolved_paths=session.unresolved_paths,
            session=session,
            require_completion_evidence=require_completion_evidence,
        )
        return bool(result)

    return _invoke


__all__ = ["default_invoker"]
