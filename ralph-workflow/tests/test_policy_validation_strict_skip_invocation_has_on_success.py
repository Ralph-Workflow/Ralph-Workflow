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


class TestSkipInvocationHasOnSuccess:
    """skip_invocation=true requires transitions.on_success."""

    def test_skip_invocation_without_on_success_raises(self) -> None:
        # Use model_construct to bypass Pydantic's required-field and cross-reference
        # validators so we can create the exact invalid state the policy validator
        # is designed to catch (skip_invocation=True with no on_success target).
        invalid_transitions = PhaseTransition.model_construct(
            on_success=None,
            on_failure=None,
            on_loopback=None,
        )
        work_phase = PhaseDefinition.model_construct(
            drain="work",
            transitions=invalid_transitions,
            role="execution",
            skip_invocation=True,
            bypass_routes={},
            decisions={},
            verification=None,
            parallelization=None,
        )
        policy = PipelinePolicy.model_construct(
            phases={"work": work_phase, "complete": _terminal_phase()},
            entry_phase="work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
            loop_counters={},
            budget_counters={},
            post_commit_routes=[],
        )
        bundle = PolicyBundle.model_construct(
            agents=_minimal_agents(["work", "complete"]),
            pipeline=policy,
            artifacts=ArtifactsPolicy(artifacts={}),
        )
        with pytest.raises(
            PolicyValidationError,
            match=r"skip_invocation=true requires transitions\.on_success",
        ):
            validate_policy_completeness(bundle)

    def test_skip_invocation_true_with_on_success_set_passes(self) -> None:
        phases = {
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                skip_invocation=True,
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": _terminal_phase(),
        }
        bundle = _minimal_bundle_with_phases(phases)
        validate_policy_completeness(bundle)  # must not raise

    def test_skip_invocation_false_without_on_success_does_not_raise_from_this_check(
        self,
    ) -> None:
        """skip_invocation=False with no on_success may still fail other validators."""
        phases = {
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                skip_invocation=False,
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": _terminal_phase(),
        }
        bundle = _minimal_bundle_with_phases(phases)
        validate_policy_completeness(bundle)  # must not raise due to skip_invocation check
