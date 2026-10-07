"""Recover owned integration before standalone commit generation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.display.parallel_display import resolve_active_display
from ralph.pipeline.auto_integrate_agent import (
    build_agent_conflict_resolver,
    build_agent_rebase_stop_resolver,
)
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.integration_resolution import (
    inspect_integration_resolution,
    retained_integration_reason,
)
from ralph.pipeline.integration_resolution_types import IntegrationResolutionVerdict
from ralph.pipeline.rebase_state import RebaseState
from ralph.policy.loader import load_policy_for_workspace_scope
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.agents.registry import AgentRegistry
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.factory import PipelineDeps


def prepare_commit_integration(
    root: Path,
    config: UnifiedConfig,
    deps: PipelineDeps,
    registry: AgentRegistry,
    display_context: DisplayContext,
    *,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
) -> IntegrationResolutionVerdict:
    """Finish an existing obligation or return its visible dispatch blocker.

    This seam never starts integration in an unowned workspace. Recovery uses
    the same supervised resolvers and Git proof as pipeline startup, including
    when new integration is disabled. Unresolved evidence remains on disk.
    """
    scope = WorkspaceScope(root)
    state = RebaseState()
    if retained_integration_reason(root) is not None:
        policy = deps.policy_bundle
        if policy is None:
            policy = (
                deps.policy_bundle_factory(scope, config)
                if deps.policy_bundle_factory is not None
                else load_policy_for_workspace_scope(scope, config)
            )
        display = resolve_active_display(None, display_context)
        display.emit_status("Recovering retained integration before commit generation")
        state = integrate_before_planning(
            config, scope, state,
            conflict_resolver=conflict_resolver or build_agent_conflict_resolver(
                policy_bundle=policy, registry=registry, display=display,
                config=config, pipeline_deps=deps, workspace_scope=scope,
                display_context=display_context,
            ),
            rebase_stop_resolver=rebase_stop_resolver or build_agent_rebase_stop_resolver(
                policy_bundle=policy, registry=registry, display=display,
                config=config, pipeline_deps=deps, workspace_scope=scope,
                display_context=display_context,
            ),
            display=display,
        ) or state
    verdict = inspect_integration_resolution(root, state)
    if not verdict.dispatch_allowed and state.last_reason:
        return IntegrationResolutionVerdict(
            verdict.status, (state.last_reason, *verdict.reasons), verdict.recovery_executor,
        )
    return verdict


def commit_integration_blocker(verdict: IntegrationResolutionVerdict) -> str:
    return (
        f"Commit generation blocked by unfinished integration: {'; '.join(verdict.reasons)}. "
        "Recovery evidence preserved; resume Ralph to continue recovery."
    )
