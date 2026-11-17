from __future__ import annotations

from typing import TYPE_CHECKING

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
    from ralph.pipeline.resolution_outcome import StopResolutionOutcome
    from ralph.policy.models import PolicyBundle
    from ralph.workspace.scope import WorkspaceScope


def default_invoker(
    *,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps | None = None,
    workspace_scope: WorkspaceScope,
    policy_bundle: PolicyBundle,
    display: ParallelDisplay | None,
    display_context: DisplayContext | None,
    limits: ConflictResolutionConfig,
    round_cap: int,
    clock: MonotonicClock,
    session: ResolutionSession,
    stop: RebaseStop | None,
    root: Path,
    target: str,
    require_completion_evidence: bool = True,
) -> ResolutionInvoker:
    del limits, round_cap, clock, stop, root, target

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> StopResolutionOutcome:
        del round_index
        from ralph.pipeline.conflict_resolution import driver as _driver

        patched = _driver.invoke_resolution_agent
        deps = pipeline_deps
        if deps is None:
            from ralph.display.context import make_display_context
            from ralph.pipeline.factory import DefaultPipelineFactory

            deps = DefaultPipelineFactory().build(config, display_context or make_display_context())
        attempt = patched(
            agent_name=agent_name,
            prompt_path=prompt_path,
            config=config,
            pipeline_deps=deps,
            workspace_scope=workspace_scope,
            policy_bundle=policy_bundle,
            display=display,
            display_context=display_context,
            unresolved_paths=session.unresolved_paths,
            session=session,
            require_completion_evidence=require_completion_evidence,
        )
        return _driver.stop_outcome_from_attempt_result(session, attempt, agent_name=agent_name)

    return _invoke


__all__ = ["default_invoker"]
