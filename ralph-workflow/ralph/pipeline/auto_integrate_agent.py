from __future__ import annotations

__doc__ = "Endpoint-merge mode creates a merge commit while rebase uses git rebase --continue."

import re
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from loguru import logger

from ralph.config._toml_fallback import describe_config_failure, emit_config_failure_warning
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.conflict_resolution import (
    run_conflict_resolution_pipeline,
    run_rebase_conflict_resolution_outcome,
)
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.rebase_loop import active_rebase_resolution_session
from ralph.pipeline.conflict_resolution.session import resolution_chain_agents
from ralph.pipeline.resolution_outcome import (
    ConfigFailureOutcome,
    NextStrategyOutcome,
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
    UnknownOutcome,
    WaitingOutcome,
)
from ralph.workspace.context import WorkspaceContextError, workspace_context


@runtime_checkable
class _WarnLineDisplay(Protocol):
    def emit_warn_line(self, unit_id: str, tag: str, message: str) -> None: ...


class _AutoIntegrateError(Exception):
    __slots__ = ("original",)

    def __init__(self, original: Exception) -> None:
        self.original = original
        super().__init__(str(original))


def _run_auto_integrate_operation[T](operation: Callable[[], T]) -> T:
    try:
        return operation()
    except Exception as exc:
        raise _AutoIntegrateError(exc) from exc


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.config.models import AgentConfig, UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStop, RebaseStopResolver
    from ralph.pipeline.factory import PipelineDeps
    from ralph.pipeline.resolution_outcome import StopResolutionOutcome
    from ralph.policy.models import PolicyBundle
    from ralph.workspace.scope import WorkspaceScope

    class _SupportsAgentLookup(Protocol):
        def get(self, name: str) -> AgentConfig | None: ...


_WARN_CHANNEL = "auto-integrate"


def emit_integration_warn_line(display: object, message: str) -> None:
    if not isinstance(display, _WarnLineDisplay):
        return
    try:
        _run_auto_integrate_operation(lambda: display.emit_warn_line("run", _WARN_CHANNEL, message))
    except _AutoIntegrateError as failure:
        logger.debug("auto_integrate: operator warn line failed (non-fatal): {}", failure.original)


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

    def _resolver(root: Path, target: str) -> StopResolutionOutcome:
        effective_pipeline_deps = pipeline_deps
        effective_workspace_scope = workspace_scope
        if effective_pipeline_deps is None or effective_workspace_scope is None:
            missing = "pipeline_deps" if effective_pipeline_deps is None else "workspace_scope"
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
            return NextStrategyOutcome("completion_in_place")
        if not _any_chain_agent_installed(policy_bundle, registry):
            logger.warning(
                "auto_integrate: no agent of the rebase-conflict-resolution "
                "chain is installed; declining to resolve"
            )
            emit_integration_warn_line(
                display,
                "conflict resolution unavailable: no rebase-conflict-resolution agent installed",
            )
            return NextStrategyOutcome("completion_in_place")
        try:
            raw_obj = _run_auto_integrate_operation(
                lambda: run_conflict_resolution_pipeline(
                    root=root,
                    target=target,
                    config=config,
                    pipeline_deps=effective_pipeline_deps,
                    workspace_scope=effective_workspace_scope,
                    policy_bundle=policy_bundle,
                    display=display,
                    display_context=display_context,
                    strategy_history=strategy_history,
                )
            )
            return _wrap_stop_resolution_outcome(raw_obj)
        except _AutoIntegrateError as failure:
            exc = failure.original
            logger.warning("auto_integrate: conflict-resolution pipeline raised: {}", exc)
            emit_integration_warn_line(
                display, f"conflict resolution failed: resolution pipeline raised {exc}"
            )
            return UnknownOutcome(exc)

    return _resolver


def _wrap_stop_resolution_outcome(raw_obj: object) -> StopResolutionOutcome:
    if isinstance(raw_obj, bool):
        return (
            ResolvedOutcome()
            if raw_obj
            else RetryLaterOutcome(on_change="rebase resolution failed")
        )
    if isinstance(
        raw_obj,
        (
            ResolvedOutcome,
            WaitingOutcome,
            RetryLaterOutcome,
            NextStrategyOutcome,
            ConfigFailureOutcome,
            UnknownOutcome,
        ),
    ):
        return raw_obj
    return UnknownOutcome(RuntimeError(f"unexpected resolution outcome {raw_obj!r}"))


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

    def _resolver(root: Path, target: str, stop: RebaseStop) -> StopResolutionOutcome:
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
            return NextStrategyOutcome("completion_in_place")
        try:
            raw_obj: object = _resolve_within_target_context(
                root=root,
                target=target,
                stop=stop,
                pipeline_deps=pipeline_deps,
                display=display,
                display_context=display_context,
                strategy_history=strategy_history,
            )
            return _wrap_stop_resolution_outcome(raw_obj)
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
            return RetryLaterOutcome(
                on_change=reason_text,
                terminal_reason=ResolutionTerminationReason.CONFIG_FAILURE,
            )
        except Exception as exc:
            logger.warning(
                "auto_integrate: could not use the target worktree's own "
                "configuration for rebase conflict resolution ({}); declining "
                "to resolve the rebase",
                exc,
            )
            is_config = _is_config_exception(exc)
            cfg_path = _extract_config_path(exc)
            target_path = cfg_path if cfg_path else str(root / ".agent" / "ralph-workflow.toml")
            if is_config:
                failure_cause = ResolutionFailureCause.CONFIG_FAILURE
                term_reason = ResolutionTerminationReason.CONFIG_FAILURE
                reason_text = f"fix {target_path} then run ralph --check-config"
                emit_config_failure_warning(
                    target_path,
                    kind="ralph-workflow",
                    error_detail=describe_config_failure(exc),
                    behaviour=(
                        "strict configuration behaviour in effect "
                        "(this file has no built-in default fallback)"
                    ),
                    fix_hint=f"To continue: {reason_text}",
                    exc=exc,
                )
            else:
                failure_cause = ResolutionFailureCause.CONTEXT_FAILURE
                term_reason = ResolutionTerminationReason.CONTEXT_FAILURE
                reason_text = f"target worktree context failed: {exc}"
            session = active_rebase_resolution_session()
            if session is not None:
                session.charge_conflict_budget = False
                session.terminal_reason = term_reason
                session.last_failure_cause = failure_cause
                session.exhaustion_reason = reason_text
            emit_integration_warn_line(
                display,
                f"rebase conflict resolution unavailable: {reason_text}",
            )
            return RetryLaterOutcome(
                on_change=reason_text,
                terminal_reason=term_reason,
                failure_cause=failure_cause,
            )

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
) -> StopResolutionOutcome | bool:
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
            return NextStrategyOutcome("completion_in_place")
        try:
            if strategy_history:
                return run_rebase_conflict_resolution_outcome(
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
            return run_rebase_conflict_resolution_outcome(
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
            return UnknownOutcome(exc)


def _any_chain_agent_installed(policy_bundle: PolicyBundle, registry: _SupportsAgentLookup) -> bool:
    return any(
        registry.get(agent_name) is not None
        for agent_name in resolution_chain_agents(policy_bundle)
    )


_CONFIG_PATH_PATTERN = re.compile(
    r"""(?:['"`(\[\s]|^)([^\s'"`()\[\]:;,<>]*?(?:agents|pipeline|artifacts|mcp|portfolio|policy-portfolio|ralph-workflow[^\s'"`()\[\]:;,<>]*)\.toml)(?:['"`\)\]:;,]|\s|$)""",
    re.IGNORECASE,
)

_FIX_PATTERN = re.compile(
    r"""fix\s+([^\s]+)\s+then run ralph --check-config""",
    re.IGNORECASE,
)

_COULD_NOT_READ_PATTERN = re.compile(
    r"""Ralph could not read (.*?):""",
    re.IGNORECASE,
)

_CONFIG_FILE_PATTERN = re.compile(
    r"""ralph-workflow(?:-[^\s'"`()\[\]:;,<>]+)?\.toml""",
    re.IGNORECASE,
)


def _extract_config_path(exc: BaseException) -> str | None:
    for attr in ("file", "path"):
        val: object = getattr(exc, attr, None)
        if val is not None:
            return str(val)

    chain: list[BaseException] = []
    curr: BaseException | None = exc
    while curr is not None and curr not in chain:
        chain.append(curr)
        curr = curr.__cause__ or curr.__context__

    for e in chain:
        for attr in ("file", "path"):
            chain_val: object = getattr(e, attr, None)
            if chain_val is not None:
                return str(chain_val)

    for e in chain:
        msg = str(e)
        m = _FIX_PATTERN.search(msg)
        if m is not None:
            raw_fix: object = m.group(1)
            if isinstance(raw_fix, str):
                return raw_fix.strip()
        m = _COULD_NOT_READ_PATTERN.search(msg)
        if m is not None:
            raw_read: object = m.group(1)
            if isinstance(raw_read, str):
                return raw_read.strip()
        m = _CONFIG_PATH_PATTERN.search(msg)
        if m is not None:
            raw_cfg: object = m.group(1)
            if isinstance(raw_cfg, str):
                return raw_cfg.strip()

    return None


def _is_config_exception(exc: BaseException) -> bool:
    if isinstance(exc, WorkspaceContextError) or _extract_config_path(exc) is not None:
        return True
    config_types: tuple[type[BaseException], ...] = ()
    try:
        from ralph.config.loader import ConfigTomlError

        config_types = (ConfigTomlError,)
    except ImportError:
        pass
    try:
        from pydantic import ValidationError

        config_types = (*config_types, ValidationError)
    except ImportError:
        pass
    if config_types and isinstance(exc, config_types):
        return True

    chain: list[BaseException] = []
    curr: BaseException | None = exc
    while curr is not None and curr not in chain:
        chain.append(curr)
        curr = curr.__cause__ or curr.__context__

    cfg_keywords = (
        "check-config",
        "configuration",
        "ralph-workflow.toml",
        "agents.toml",
        "pipeline.toml",
        "artifacts.toml",
        "mcp.toml",
        "portfolio.toml",
    )
    for e in chain:
        if isinstance(e, WorkspaceContextError) or (config_types and isinstance(e, config_types)):
            return True
        msg = str(e).lower()
        if any(kw in msg for kw in cfg_keywords):
            return True
        if _CONFIG_FILE_PATTERN.search(msg) is not None:
            return True
    return False
