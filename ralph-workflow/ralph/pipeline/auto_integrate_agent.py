from __future__ import annotations

from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.config._toml_fallback import describe_config_failure, emit_config_failure_warning
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.conflict_resolution import (
    run_conflict_resolution_pipeline,
    run_rebase_conflict_resolution_pipeline,
)
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.rebase_loop import active_rebase_resolution_session
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
from ralph.pipeline.conflict_resolution.session import resolution_chain_agents
from ralph.workspace.context import WorkspaceContextError, workspace_context

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from typing import Protocol

    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStop, RebaseStopResolver
    from ralph.pipeline.factory import PipelineDeps
    from ralph.policy.models import PolicyBundle
    from ralph.workspace.scope import WorkspaceScope

    class _SupportsAgentLookup(Protocol):
        def get(self, name: str) -> AgentConfig | None: ...


_WARN_CHANNEL = "auto-integrate"


def emit_integration_warn_line(display: object, message: str) -> None:
    try:
        emit = cast(
            "Callable[[str, str, str], None] | None",
            getattr(display, "emit_warn_line", None),
        )
        if emit is not None:
            emit("run", _WARN_CHANNEL, message)
    except Exception as exc:
        logger.debug("auto_integrate: operator warn line failed (non-fatal): {}", exc)


def build_agent_conflict_resolver(
    *,
    policy_bundle: PolicyBundle,
    registry: _SupportsAgentLookup,
    display: ParallelDisplay,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps | None = None,
    workspace_scope: WorkspaceScope | None = None,
    display_context: DisplayContext | None = None,
    strategy_history: tuple[str, ...] = (),
) -> ConflictResolver:

    def _resolver(root: Path, target: str) -> bool:
        if pipeline_deps is None or workspace_scope is None:
            missing = "pipeline_deps" if pipeline_deps is None else "workspace_scope"
            logger.warning(
                "auto_integrate: conflict resolution unavailable ({} not "
                "threaded to this seam); declining rather than invoking an "
                "agent without a Ralph MCP session",
                missing,
            )
            emit_integration_warn_line(
                display,
                f"conflict resolution unavailable: {missing} not threaded to this seam",
            )
            return False
        if not _any_chain_agent_installed(policy_bundle, registry):
            logger.warning(
                "auto_integrate: no agent of the rebase-conflict-resolution "
                "chain is installed; declining to resolve"
            )
            emit_integration_warn_line(
                display,
                "conflict resolution unavailable: no rebase-conflict-resolution agent installed",
            )
            return False
        try:
            if strategy_history:
                return run_conflict_resolution_pipeline(
                    root=root,
                    target=target,
                    config=config,
                    pipeline_deps=pipeline_deps,
                    workspace_scope=workspace_scope,
                    policy_bundle=policy_bundle,
                    display=display,
                    display_context=display_context,
                    strategy_history=strategy_history,
                )
            return run_conflict_resolution_pipeline(
                root=root,
                target=target,
                config=config,
                pipeline_deps=pipeline_deps,
                workspace_scope=workspace_scope,
                policy_bundle=policy_bundle,
                display=display,
                display_context=display_context,
            )
        except Exception as exc:
            logger.warning("auto_integrate: conflict-resolution pipeline raised: {}", exc)
            emit_integration_warn_line(
                display, f"conflict resolution failed: resolution pipeline raised {exc}"
            )
            return False

    return _resolver


def build_agent_rebase_stop_resolver(
    *,
    policy_bundle: PolicyBundle,
    registry: _SupportsAgentLookup,
    display: ParallelDisplay,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps | None = None,
    workspace_scope: WorkspaceScope | None = None,
    display_context: DisplayContext | None = None,
    strategy_history: tuple[str, ...] = (),
) -> RebaseStopResolver:

    def _resolver(root: Path, target: str, stop: RebaseStop) -> ResolutionOutcome | bool:
        if pipeline_deps is None or workspace_scope is None:
            missing = "pipeline_deps" if pipeline_deps is None else "workspace_scope"
            logger.warning(
                "auto_integrate: rebase conflict resolution unavailable ({} not "
                "threaded to this seam); declining rather than invoking an "
                "agent without a Ralph MCP session",
                missing,
            )
            emit_integration_warn_line(
                display,
                f"rebase conflict resolution unavailable: {missing} not threaded to this seam",
            )
            return False
        try:
            return _resolve_within_target_context(
                root=root,
                target=target,
                stop=stop,
                pipeline_deps=pipeline_deps,
                display=display,
                display_context=display_context,
                strategy_history=strategy_history,
            )
        except WorkspaceContextError as exc:
            session = active_rebase_resolution_session()
            reason_text = f"fix {exc.file} then run ralph --check-config"
            if session is not None:
                session.charge_conflict_budget = False
                session.terminal_reason = ResolutionTerminationReason.CONFIG_FAILURE
                session.last_failure_cause = ResolutionFailureCause.CONFIG_FAILURE
                session.exhaustion_reason = reason_text
            emit_config_failure_warning(
                exc.file,
                kind="ralph-workflow",
                error_detail=describe_config_failure(exc),
                behaviour=(
                    "strict configuration behaviour in effect "
                    "(this file has no built-in default fallback)"
                ),
                fix_hint=f"To continue: {reason_text}",
                exc=exc,
            )
            emit_integration_warn_line(
                display,
                f"rebase conflict resolution unavailable: {reason_text}",
            )
            return ResolutionOutcome(
                succeeded=False,
                reason=reason_text,
                duration_seconds=0.0,
                last_activity_kind=None,
                last_activity_at=None,
                unresolved_paths=(),
                failure_cause=ResolutionFailureCause.CONFIG_FAILURE,
            )
        except Exception as exc:
            logger.warning(
                "auto_integrate: could not use the target worktree's own "
                "configuration for rebase conflict resolution ({}); declining "
                "to resolve the rebase",
                exc,
            )
            emit_integration_warn_line(
                display,
                f"rebase conflict resolution unavailable: target worktree context failed: {exc}",
            )
            return False

    return _resolver


def _resolve_within_target_context(
    *,
    root: Path,
    target: str,
    stop: RebaseStop,
    pipeline_deps: PipelineDeps,
    display: ParallelDisplay,
    display_context: DisplayContext | None,
    strategy_history: tuple[str, ...],
) -> ResolutionOutcome | bool:
    with workspace_context(root) as target_ctx:
        target_config = target_ctx.config
        target_policy = target_ctx.policy_bundle
        target_registry = target_ctx.registry
        target_scope = target_ctx.target_scope
        if not _any_chain_agent_installed(target_policy, target_registry):
            logger.warning(
                "auto_integrate: no agent of the rebase-conflict-resolution "
                "chain is installed on the target worktree; declining to resolve the rebase"
            )
            emit_integration_warn_line(
                display,
                "rebase conflict resolution unavailable: no "
                "rebase-conflict-resolution agent installed on the target worktree",
            )
            return False
        try:
            if strategy_history:
                return run_rebase_conflict_resolution_pipeline(
                    root=root,
                    target=target,
                    stop=stop,
                    config=target_config,
                    pipeline_deps=pipeline_deps,
                    workspace_scope=target_scope,
                    policy_bundle=target_policy,
                    display=display,
                    display_context=display_context,
                    strategy_history=strategy_history,
                )
            return run_rebase_conflict_resolution_pipeline(
                root=root,
                target=target,
                stop=stop,
                config=target_config,
                pipeline_deps=pipeline_deps,
                workspace_scope=target_scope,
                policy_bundle=target_policy,
                display=display,
                display_context=display_context,
            )
        except Exception as exc:
            logger.warning(
                "auto_integrate: rebase conflict-resolution pipeline raised: {}",
                exc,
            )
            emit_integration_warn_line(
                display,
                f"rebase conflict resolution failed: resolution pipeline raised {exc}",
            )
            return False


def _any_chain_agent_installed(policy_bundle: PolicyBundle, registry: _SupportsAgentLookup) -> bool:
    return any(
        registry.get(agent_name) is not None
        for agent_name in resolution_chain_agents(policy_bundle)
    )
