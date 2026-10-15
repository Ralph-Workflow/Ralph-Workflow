"""Default conflict-resolution invocation builder."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.operator_cap import MonotonicClock
from ralph.pipeline.conflict_resolution.operator_cap import (
    remaining_operator_cap as _remaining_operator_cap,
)
from ralph.pipeline.conflict_resolution.status import (
    ResolutionStatusReporter,
    emit_conflict_phase_line,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.conflict_resolution_config import ConflictResolutionConfig
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.conflict_resolution.driver import ResolutionInvoker
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
    """Build a conflict-only invocation that shares session cap and status context."""
    cap = session.total_resolution_cap_seconds
    interval = limits.status_interval_seconds

    def _invoke(agent_name: str, prompt_path: Path, round_index: int) -> bool:
        # The cap is also checked between rounds, but a round spends
        # every live candidate, so it can expire inside one. A zero
        # remaining cap is rejected by the watchdog's timeout policy,
        # and that ValueError would be filed as a launch EXCEPTION --
        # naming the wrong terminal reason for the operator's own cap.
        remaining = _remaining_operator_cap(cap, session, clock)
        if remaining is not None and remaining <= 0.0:
            session.terminal_reason = ResolutionTerminationReason.OPERATOR_CAP_REACHED
            emit_conflict_phase_line(
                display,
                f"OPERATOR_CAP_REACHED: the resolution cap expired before "
                f"round {round_index} could launch {agent_name}",
            )
            return False
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
        # Late binding through the driver namespace: tests monkeypatch
        # ``driver.invoke_resolution_agent`` there.
        from ralph.pipeline.conflict_resolution import driver as _driver

        # getattr on a module is untyped; both candidates share the same
        # keyword contract (the direct function and any test double).
        # Direct attribute access so mypy sees the real signature; tests
        # monkeypatch this name on the driver module.
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
            operator_cap_seconds=remaining,
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
