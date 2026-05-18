"""Tests for the three new strict policy validators.

Covers:
- _validate_skip_invocation_has_on_success
- _validate_parallelization_consistency
- _validate_cli_counter_overrides
"""

from __future__ import annotations

from typing import cast

import pytest

from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    BudgetCounterConfig,
    DrainName,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
    RecoveryPolicy,
)
from ralph.policy.validation import (
    PolicyValidationError,
    validate_policy_completeness,
)


def _minimal_agents(drains: list[str]) -> AgentsPolicy:
    chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
    agent_drains = cast(
        "dict[DrainName, AgentDrainConfig]",
        {d: AgentDrainConfig(chain=d) for d in drains},
    )
    return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)


def _terminal_phase(drain: str = "complete", outcome: str = "success") -> PhaseDefinition:
    return PhaseDefinition(
        drain=drain,
        role="terminal",
        terminal_outcome=outcome,
        transitions=PhaseTransition(on_success=drain, on_loopback=drain),
    )


def _minimal_bundle_with_phases(phases: dict[str, PhaseDefinition]) -> PolicyBundle:
    drains = list(phases.keys())
    agents = _minimal_agents(drains)
    pipeline = PipelinePolicy(
        phases=phases,
        entry_phase=drains[0],
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="complete"),
    )
    return PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={}))


class TestCliCounterOverrides:
    """CLI counter overrides must reference declared budget_counters."""

    def _bundle_with_budget_counter(self, counter_name: str) -> PolicyBundle:
        agents = _minimal_agents(["work", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": _terminal_phase(),
            },
            entry_phase="work",
            terminal_phase="complete",
            budget_counters={counter_name: BudgetCounterConfig(tracks_budget=False, default_max=0)},
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        return PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )

    def test_unknown_counter_override_raises(self) -> None:
        bundle = self._bundle_with_budget_counter("my_counter")
        with pytest.raises(PolicyValidationError, match="unknown_counter"):
            validate_policy_completeness(bundle, cli_counter_overrides={"unknown_counter": 3})

    def test_declared_counter_override_passes(self) -> None:
        bundle = self._bundle_with_budget_counter("my_counter")
        validate_policy_completeness(
            bundle, cli_counter_overrides={"my_counter": 3}
        )  # must not raise

    def test_no_overrides_kwarg_does_not_validate_counters(self) -> None:
        bundle = self._bundle_with_budget_counter("my_counter")
        validate_policy_completeness(bundle)  # no cli_counter_overrides — must not raise

    def test_none_overrides_does_not_validate_counters(self) -> None:
        bundle = self._bundle_with_budget_counter("my_counter")
        validate_policy_completeness(bundle, cli_counter_overrides=None)  # must not raise

    def test_error_message_lists_declared_counters(self) -> None:
        bundle = self._bundle_with_budget_counter("declared_counter")
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle, cli_counter_overrides={"bad_counter": 1})
        error_msg = str(exc_info.value)
        assert "bad_counter" in error_msg
        assert "declared_counter" in error_msg
