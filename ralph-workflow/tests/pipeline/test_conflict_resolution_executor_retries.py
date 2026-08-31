"""The executor owns no retry budget for a conflict resolver and surfaces its kills typed."""

from __future__ import annotations

from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    SupervisionInfrastructureError,
)
from ralph.config.models import UnifiedConfig
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.effect_executor import recovery_attempts_for_effect, surfaces_supervision_error
from ralph.pipeline.effects.invoke_agent_effect import InvokeAgentEffect


def _effect(*, activity_only: bool) -> InvokeAgentEffect:
    return InvokeAgentEffect(
        agent_name="resolver",
        phase=PHASE_RESOLUTION,
        prompt_file="prompt.md",
        activity_only_supervision=activity_only,
    )


def test_a_conflict_resolver_gets_no_executor_level_retries() -> None:
    """The conflict-resolution driver is the only retry authority for its resolver."""
    config = UnifiedConfig.model_validate({"general": {}})
    assert recovery_attempts_for_effect(_effect(activity_only=True), config) == 0
    assert recovery_attempts_for_effect(_effect(activity_only=False), config) >= 1


def test_supervision_kills_reach_the_conflict_session_typed() -> None:
    inactivity = AgentInactivityTimeoutError("resolver", 900.0)
    infrastructure = SupervisionInfrastructureError("resolver", "relay died")
    ordinary = AgentInvocationError("resolver", 1, "exit 1")

    assert surfaces_supervision_error(_effect(activity_only=True), inactivity) is True
    assert surfaces_supervision_error(_effect(activity_only=True), infrastructure) is True
    assert surfaces_supervision_error(_effect(activity_only=True), ordinary) is False
    assert surfaces_supervision_error(_effect(activity_only=False), inactivity) is False
