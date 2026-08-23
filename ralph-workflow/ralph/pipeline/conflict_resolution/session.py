"""Run one conflict-resolution attempt through the normal MCP invocation seam."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke import AgentInactivityTimeoutError, SupervisionInfrastructureError
from ralph.pipeline import effect_executor as _effect_executor_module
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.attempt_fault import (
    INFRASTRUCTURE_TERMINATION_REASONS,
    classify_ralph_origin_fault,
)
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.factory import PipelineDeps
    from ralph.policy.models import PolicyBundle
    from ralph.workspace.scope import WorkspaceScope

__all__ = ["ResolutionSession", "invoke_resolution_agent", "resolution_chain_agents"]

_MIN_FALLBACK_CHAIN_LENGTH = 2


@dataclass
class ResolutionSession:
    """Timing and unresolved-path context spanning one complete resolution."""

    started_at: float | None = None
    unresolved_paths: tuple[str, ...] = ()
    inactivity_timeout_seconds: float | None = None
    max_rounds_per_stop: int | None = None
    max_rebase_conflict_stops: int | None = None
    max_fallback_agents: int | None = None
    total_resolution_cap_seconds: float | None = None
    terminal_reason: ResolutionTerminationReason | None = None
    last_activity_kind: str | None = None
    last_activity_at: float | None = None
    last_duration_seconds: float | None = None
    chain_cursor: int = 0
    charge_conflict_budget: bool = True
    dead_tool_surfaces: tuple[str, ...] = ()


def resolution_chain_agents(policy_bundle: PolicyBundle) -> tuple[str, ...]:
    """Return the configured conflict-resolution candidate chain."""
    drain_binding = policy_bundle.agents.agent_drains.get(PHASE_RESOLUTION)
    if drain_binding is None:
        return ()
    chain_config = policy_bundle.agents.agent_chains.get(drain_binding.chain)
    if chain_config is None:
        return ()
    agents = tuple(chain_config.agents)
    if len(agents) < _MIN_FALLBACK_CHAIN_LENGTH:
        logger.warning(
            "conflict_resolution: drain '{}' is bound to a one-agent chain '{}'; "
            "there is no fallback candidate if this resolver fails",
            PHASE_RESOLUTION,
            drain_binding.chain,
        )
    return agents


def invoke_resolution_agent(
    *,
    agent_name: str,
    prompt_path: Path,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps,
    workspace_scope: WorkspaceScope,
    policy_bundle: PolicyBundle,
    display: ParallelDisplay | None,
    display_context: DisplayContext | None,
    operator_cap_seconds: float | None = None,
    inactivity_timeout_seconds: float | None = None,
    status_interval_seconds: float | None = None,
    activity_status_listener: Callable[[object], None] | None = None,
    unresolved_paths: tuple[str, ...] = (),
    session: ResolutionSession | None = None,
) -> bool:
    """Run one activity-only conflict attempt using the chain's retry budget."""
    if session is not None and agent_name in session.dead_tool_surfaces:
        logger.warning(
            "conflict_resolution: refusing to re-enter known-dead tool surface '{}'",
            agent_name,
        )
        session.terminal_reason = ResolutionTerminationReason.TOOL_SURFACE_DEAD
        session.charge_conflict_budget = False
        return False
    wrapped_listener = _wrap_activity_listener(
        activity_status_listener, session, agent_name=agent_name
    )
    effect = InvokeAgentEffect(
        agent_name=agent_name,
        phase=PHASE_RESOLUTION,
        prompt_file=str(prompt_path),
        drain=PHASE_RESOLUTION,
        chain_name=PHASE_RESOLUTION,
        requires_completion_evidence=False,
        activity_only_supervision=True,
        activity_only_operator_cap_seconds=operator_cap_seconds,
        activity_only_status_interval_seconds=status_interval_seconds,
        activity_status_listener=wrapped_listener,
    )
    conflict_limits = config.conflict_resolution.model_copy(
        update={
            "inactivity_timeout_seconds": (
                inactivity_timeout_seconds
                if inactivity_timeout_seconds is not None
                else config.conflict_resolution.inactivity_timeout_seconds
            )
        }
    )
    conflict_config = config.model_copy(
        update={
            "conflict_resolution": conflict_limits,
        }
    )
    try:
        event = _effect_executor_module.execute_agent_effect(
            effect,
            conflict_config,
            pipeline_deps,
            workspace_scope,
            display=display,
            display_context=display_context,
            policy_bundle=policy_bundle,
            run_id=None,
        )
    except AgentInactivityTimeoutError as exc:
        _record_resolution_termination(session, exc)
        _log_resolution_termination(exc, unresolved_paths)
        return False
    except SupervisionInfrastructureError as exc:
        _record_resolution_termination(session, exc)
        _log_resolution_termination(exc, unresolved_paths)
        return False
    except Exception as exc:
        _record_resolution_exception(session)
        logger.warning("conflict_resolution: agent '{}' could not be launched: {}", agent_name, exc)
        return False
    return event == PipelineEvent.AGENT_SUCCESS


def _record_resolution_termination(session: ResolutionSession | None, exc: Exception) -> None:
    """Persist typed invocation failure metadata for the driver's outcome line."""
    if session is None:
        return
    if isinstance(exc, AgentInactivityTimeoutError):
        diagnostic = exc.diagnostic
        reason = exc.reason
        session.terminal_reason = (
            ResolutionTerminationReason.OPERATOR_CAP_REACHED
            if reason == WatchdogFireReason.OPERATOR_CAP_REACHED
            else ResolutionTerminationReason.CONFLICT_INACTIVITY
        )
        raw_kind = diagnostic.get("last_activity_kind")
        session.last_activity_kind = raw_kind if isinstance(raw_kind, str) else None
        raw_at = diagnostic.get("last_activity_at")
        session.last_activity_at = float(raw_at) if isinstance(raw_at, (int, float)) else None
        raw_duration = diagnostic.get("invocation_elapsed_seconds")
        session.last_duration_seconds = (
            float(raw_duration) if isinstance(raw_duration, (int, float)) else exc.timeout_seconds
        )
        return
    session.terminal_reason = ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE


def _record_resolution_exception(session: ResolutionSession | None) -> None:
    """Preserve a launch failure rather than relabeling it as a declined candidate."""
    if session is not None:
        session.terminal_reason = ResolutionTerminationReason.EXCEPTION


def _log_resolution_termination(exc: Exception, unresolved_paths: tuple[str, ...]) -> None:
    """Emit an operator-actionable resolution termination diagnostic."""
    fields: dict[str, str | int | float | bool | list[object]]
    if isinstance(exc, AgentInactivityTimeoutError):
        reason_value = (
            exc.reason.value
            if isinstance(exc.reason, WatchdogFireReason)
            else "CONFLICT_INACTIVITY"
        )
        fields = exc.diagnostic
    else:
        reason_value = "SUPERVISION_INFRASTRUCTURE_FAILURE"
        fields = {}
    logger.warning(
        "conflict_resolution termination: reason={}; last_activity_kind={}; "
        "last_activity_at={}; duration_seconds={}; unresolved_paths={}",
        reason_value,
        fields.get("last_activity_kind", "none"),
        fields.get("last_activity_at", "never"),
        fields.get("invocation_elapsed_seconds", "unknown"),
        ", ".join(unresolved_paths),
    )


def _wrap_activity_listener(
    listener: Callable[[object], None] | None,
    session: ResolutionSession | None,
    *,
    agent_name: str,
) -> Callable[[object], None] | None:
    """Escalate Ralph-origin faults from activity events instead of treating them as life."""

    def _observe(event: object) -> None:
        reason = classify_ralph_origin_fault(str(event))
        if reason is not None and session is not None:
            session.terminal_reason = reason
            session.charge_conflict_budget = False
            if reason in INFRASTRUCTURE_TERMINATION_REASONS:
                remembered = list(session.dead_tool_surfaces)
                if agent_name not in remembered:
                    remembered.append(agent_name)
                session.dead_tool_surfaces = tuple(remembered)
            return
        if listener is not None:
            listener(event)

    return _observe
