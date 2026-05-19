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
    DrainName,
    PhaseDefinition,
    PhaseParallelization,
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


class TestParallelizationConsistency:
    """parallelization.max_work_units must be >= max_parallel_workers."""

    def test_max_work_units_less_than_max_parallel_workers_raises(self) -> None:
        phases = {
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                transitions=PhaseTransition(on_success="complete"),
                parallelization=PhaseParallelization(
                    max_parallel_workers=5,
                    max_work_units=3,
                ),
            ),
            "complete": _terminal_phase(),
        }
        bundle = _minimal_bundle_with_phases(phases)
        with pytest.raises(
            PolicyValidationError,
            match=r"max_work_units.*must be >=.*max_parallel_workers",
        ):
            validate_policy_completeness(bundle)

    def test_max_work_units_equal_to_max_parallel_workers_passes(self) -> None:
        phases = {
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                transitions=PhaseTransition(on_success="complete"),
                parallelization=PhaseParallelization(
                    max_parallel_workers=4,
                    max_work_units=4,
                ),
            ),
            "complete": _terminal_phase(),
        }
        bundle = _minimal_bundle_with_phases(phases)
        validate_policy_completeness(bundle)  # must not raise

    def test_max_work_units_greater_than_max_parallel_workers_passes(self) -> None:
        phases = {
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                transitions=PhaseTransition(on_success="complete"),
                parallelization=PhaseParallelization(
                    max_parallel_workers=2,
                    max_work_units=10,
                ),
            ),
            "complete": _terminal_phase(),
        }
        bundle = _minimal_bundle_with_phases(phases)
        validate_policy_completeness(bundle)  # must not raise

    def test_phase_without_parallelization_is_not_checked(self) -> None:
        phases = {
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": _terminal_phase(),
        }
        bundle = _minimal_bundle_with_phases(phases)
        validate_policy_completeness(bundle)  # must not raise
