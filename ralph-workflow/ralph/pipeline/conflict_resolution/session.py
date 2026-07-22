"""Runs one resolution round inside a REAL Ralph MCP session.

This module is the fix for the defect that made conflict resolution
structurally unable to work. The previous resolver called
``ralph.agents.invoke.invoke_agent`` directly with no ``extra_env``, so
``RALPH_MCP_ENDPOINT`` was never set and no Ralph MCP session existed.
Three things follow from that, all of them fatal:

* the exec-policy git denial in ``ralph.mcp.tools.exec`` never bound, so
  the agent could abort or commit the merge Ralph owns;
* ``declare_complete`` was not in the agent's tool surface at all, so
  ``requires_completion_evidence`` could not be demanded and a clean
  instant exit counted as "resolved";
* nothing was recorded: output was consumed and discarded.

Routing the invocation through
:func:`ralph.pipeline.effect_executor.execute_agent_effect` -- the same
seam :mod:`ralph.project_policy.cli_integration` uses for its
out-of-graph phases -- builds the session bridge and injects
``RALPH_MCP_ENDPOINT`` / ``RALPH_MCP_RUN_ID``, so all three become real.

TIMEOUT SEAM. ``execute_agent_effect`` derives both watchdogs from
``config.general`` inside ``_build_attempt_invoke_options``; its ``**opts``
surface carries no timeout keys. The per-attempt wall-clock share is
therefore applied by handing it a config whose
``general.agent_max_session_seconds`` is overridden, which is the value
that function reads. That share is a slice of the integration-wide
``auto_integrate_resolve_timeout_seconds`` deadline, so it is normally an
order of magnitude SMALLER than the run-wide session defaults, and the
two values ``GeneralConfig`` orders against the ceiling --
``agent_idle_timeout_seconds`` and ``agent_session_soft_wrapup_seconds``
-- are pulled down with it rather than left above it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeGuard

from loguru import logger

from ralph.pipeline import effect_executor as _effect_executor_module
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.factory import PipelineDeps
    from ralph.policy.models import PolicyBundle
    from ralph.workspace.scope import WorkspaceScope

__all__ = [
    "invoke_resolution_agent",
    "resolution_chain_agents",
    "with_session_ceiling",
]

#: Where the soft-wrapup nag lands inside a bounded attempt when the
#: configured threshold does not fit under the ceiling. Strictly < 1.0:
#: ``GeneralConfig`` requires ``soft_wrapup < max_session``.
_WRAPUP_FRACTION = 0.8


def resolution_chain_agents(policy_bundle: PolicyBundle) -> tuple[str, ...]:
    """Agents bound to the resolution drain, in fallback order.

    Empty when the drain or its chain is missing, in which case the
    caller declines rather than guessing an agent.
    """
    drain_binding = policy_bundle.agents.agent_drains.get(PHASE_RESOLUTION)
    if drain_binding is None:
        return ()
    chain_config = policy_bundle.agents.agent_chains.get(drain_binding.chain)
    if chain_config is None or not chain_config.agents:
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
    max_session_seconds: float,
) -> bool:
    """Run ONE resolution round through a real Ralph MCP session.

    Args:
        agent_name: Agent to invoke, from the resolution drain's chain.
        prompt_path: Rendered conflict-only prompt for this round.
        config: Run configuration; the wall-clock share is applied to a
            copy of it.
        pipeline_deps: Pipeline dependency bundle.
        workspace_scope: Workspace the merge is in progress in.
        policy_bundle: Resolved policy, supplying the drain's capabilities.
        display: Active display, when there is one.
        display_context: Display context, when there is one.
        max_session_seconds: Wall-clock ceiling for THIS round.

    Returns:
        Whether the invocation reported success. Never raises: a launch
        failure is this round's failure, not the run's, so the caller can
        fall back to the next chain candidate or the next round.
    """
    effect = InvokeAgentEffect(
        agent_name=agent_name,
        phase=PHASE_RESOLUTION,
        prompt_file=str(prompt_path),
        drain=PHASE_RESOLUTION,
        chain_name=PHASE_RESOLUTION,
        # The whole point of routing through a real session: a clean exit
        # WITHOUT a declare_complete sentinel is a failed round, not a
        # resolution. The drain's development class grants artifact.submit,
        # which handle_declare_complete requires.
        requires_completion_evidence=True,
    )
    try:
        event = _effect_executor_module.execute_agent_effect(
            effect,
            with_session_ceiling(config, max_session_seconds),
            pipeline_deps,
            workspace_scope,
            display=display,
            display_context=display_context,
            policy_bundle=policy_bundle,
            # A fresh run_id per resolution session: its completion
            # sentinel and artifact receipts are scoped to it and cannot
            # collide with the parent run's.
            run_id=None,
        )
    except Exception as exc:
        logger.warning(
            "conflict_resolution: agent '{}' could not be launched: {}",
            agent_name,
            exc,
        )
        return False
    return event == PipelineEvent.AGENT_SUCCESS


def with_session_ceiling(
    config: UnifiedConfig, max_session_seconds: float
) -> UnifiedConfig:
    """Return ``config`` with this attempt's wall-clock ceiling applied.

    An earlier version DECLINED the override whenever the requested share
    sat below the run-wide idle watchdog or soft-wrapup threshold, on the
    grounds that an inconsistent ceiling is worse than the configured
    one. With the shipped defaults that is every share the integration
    ever asks for -- 300 s against a 300 s idle watchdog and a 3,000 s
    wrapup -- so the attempt silently kept the unrelated 3,300 s run-wide
    maximum and the integration ceiling bounded nothing at all. The
    dependent watchdogs are lowered alongside the ceiling instead, which
    preserves the ordering
    :class:`~ralph.config.general_config.GeneralConfig` validates
    (``soft_wrapup < max_session`` and ``max_session >= idle``) while
    making the share the value that actually cuts the session.

    Args:
        config: Run configuration to derive the bounded copy from.
        max_session_seconds: This attempt's share of the pipeline
            deadline. A non-positive share cannot be expressed as a valid
            ceiling, so ``config`` is returned unchanged.

    Returns:
        A copy whose ``general`` section is internally consistent and
        whose session maximum is ``max_session_seconds``.
    """
    if max_session_seconds <= 0.0:
        return config
    general = config.general.model_copy(
        update=_ceiling_updates(config.general, max_session_seconds)
    )
    return config.model_copy(update={"general": general})


def _ceiling_updates(general: object, ceiling: float) -> dict[str, object]:
    """Field overrides that make ``ceiling`` a VALID session maximum.

    Args:
        general: The ``general`` config section, read defensively so a
            partially-constructed config in a test still yields a ceiling.
        ceiling: The session maximum to impose; strictly positive.

    Returns:
        The ``GeneralConfig`` field overrides to copy in.
    """
    updates: dict[str, object] = {"agent_max_session_seconds": ceiling}
    idle: object = getattr(general, "agent_idle_timeout_seconds", None)
    if _is_number(idle) and float(idle) > ceiling:
        updates["agent_idle_timeout_seconds"] = ceiling
    wrapup: object = getattr(general, "agent_session_soft_wrapup_seconds", None)
    if _is_number(wrapup) and float(wrapup) >= ceiling:
        # Strictly below the ceiling, so the agent is still nagged to wrap
        # up before the force-cut rather than at the same instant.
        updates["agent_session_soft_wrapup_seconds"] = ceiling * _WRAPUP_FRACTION
    return updates


def _is_number(value: object) -> TypeGuard[float]:
    """Whether ``value`` is a real numeric config value (never a bool)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)
