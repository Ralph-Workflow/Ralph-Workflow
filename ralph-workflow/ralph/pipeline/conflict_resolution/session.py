"""Run one conflict-resolution attempt through the normal MCP invocation seam."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke import AgentInactivityTimeoutError, SupervisionInfrastructureError
from ralph.pipeline import effect_executor as _effect_executor_module
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


@dataclass
class ResolutionSession:
    """Timing and unresolved-path context spanning one complete resolution."""

    started_at: float | None = None
    unresolved_paths: tuple[str, ...] = ()
    max_rebase_conflict_stops: int | None = None


def resolution_chain_agents(policy_bundle: PolicyBundle) -> tuple[str, ...]:
    """Return the configured conflict-resolution candidate chain."""
    drain_binding = policy_bundle.agents.agent_drains.get(PHASE_RESOLUTION)
    if drain_binding is None:
        return ()
    chain_config = policy_bundle.agents.agent_chains.get(drain_binding.chain)
    if chain_config is None:
        return ()
    return tuple(chain_config.agents)


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
    status_interval_seconds: float | None = None,
    activity_status_listener: Callable[[object], None] | None = None,
    unresolved_paths: tuple[str, ...] = (),
) -> bool:
    """Run one activity-only conflict attempt with no generic same-agent recovery."""
    effect = InvokeAgentEffect(
        agent_name=agent_name,
        phase=PHASE_RESOLUTION,
        prompt_file=str(prompt_path),
        drain=PHASE_RESOLUTION,
        chain_name=PHASE_RESOLUTION,
        requires_completion_evidence=True,
        activity_only_supervision=True,
        activity_only_operator_cap_seconds=operator_cap_seconds,
        activity_only_status_interval_seconds=status_interval_seconds,
        activity_status_listener=activity_status_listener,
    )
    conflict_config = config.model_copy(
        update={"general": config.general.model_copy(update={"max_same_agent_retries": 0})}
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
        _log_resolution_termination(exc, unresolved_paths)
        return False
    except SupervisionInfrastructureError as exc:
        _log_resolution_termination(exc, unresolved_paths)
        return False
    except Exception as exc:
        logger.warning("conflict_resolution: agent '{}' could not be launched: {}", agent_name, exc)
        return False
    return event == PipelineEvent.AGENT_SUCCESS


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
