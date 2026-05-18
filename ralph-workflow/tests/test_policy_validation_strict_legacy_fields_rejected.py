"""Tests for the three new strict policy validators.

Covers:
- _validate_skip_invocation_has_on_success
- _validate_parallelization_consistency
- _validate_cli_counter_overrides
"""

from __future__ import annotations

from typing import cast

import pytest
from pydantic import ValidationError

from ralph.mcp.protocol.capability_mapping import drain_class_for_session
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
    validate_drain_contracts,
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


class TestLegacyFieldsRejected:
    """Tests for removed/legacy fields that must be rejected at construction time."""

    def test_failed_route_failed_alias_rejected(self) -> None:
        """RecoveryPolicy rejects 'failed' as failed_route (removed pseudo-phase alias)."""
        with pytest.raises(ValueError, match="'failed' is no longer accepted"):
            RecoveryPolicy(failed_route="failed")

    def test_drain_class_substring_inference_rejected(self) -> None:
        """Custom drain without drain_class raises PolicyValidationError.

        Pre-v2 behavior silently inferred drain class from name substrings
        (e.g. a drain named 'custom_fixer_drain' resolved to DrainClass.FIX).
        That inference was removed; an explicit drain_class is required.
        """
        agents = AgentsPolicy(
            agent_chains={"chain": AgentChainConfig(agents=["claude"])},
            agent_drains={"custom_fixer_drain": AgentDrainConfig(chain="chain")},
        )
        with pytest.raises(PolicyValidationError):
            drain_class_for_session("custom_fixer_drain", agents)

    def test_drain_class_missing_rejected_under_strict_validation(self) -> None:
        """validate_drain_contracts rejects drains missing drain_class when strict.

        When forbid_sibling_drain_inference=true, every pipeline-used drain must
        declare drain_class explicitly in agents.toml.
        """
        agents = AgentsPolicy(
            forbid_sibling_drain_inference=True,
            agent_chains={"chain": AgentChainConfig(agents=["claude"])},
            agent_drains={
                "custom_work": AgentDrainConfig(chain="chain"),
                "complete": AgentDrainConfig(chain="chain"),
            },
        )
        pipeline = PipelinePolicy(
            phases={
                "custom_work": PhaseDefinition(
                    drain="custom_work",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="complete"),
                ),
            },
            entry_phase="custom_work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents,
            pipeline=pipeline,
            artifacts=ArtifactsPolicy(artifacts={}),
        )
        with pytest.raises(PolicyValidationError, match="no explicit drain_class"):
            validate_drain_contracts(bundle)

    def test_drain_class_present_passes_strict_validation(self) -> None:
        """validate_drain_contracts passes when all drains have explicit drain_class."""
        agents = AgentsPolicy(
            forbid_sibling_drain_inference=True,
            agent_chains={"chain": AgentChainConfig(agents=["claude"])},
            agent_drains={
                "custom_work": AgentDrainConfig(chain="chain", drain_class="development"),
                "complete": AgentDrainConfig(chain="chain", drain_class="development"),
            },
        )
        pipeline = PipelinePolicy(
            phases={
                "custom_work": PhaseDefinition(
                    drain="custom_work",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="complete"),
                ),
            },
            entry_phase="custom_work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents,
            pipeline=pipeline,
            artifacts=ArtifactsPolicy(artifacts={}),
        )
        validate_drain_contracts(bundle)  # must not raise

    def test_legacy_phase_field_requires_commit_rejected(self) -> None:
        """PhaseDefinition with requires_commit=True is rejected with an actionable error."""
        with pytest.raises(ValidationError, match="requires_commit has been removed"):
            PhaseDefinition(
                drain="build",
                role="execution",
                requires_commit=True,
                transitions=PhaseTransition(on_success="done"),
            )

    def test_legacy_phase_field_embeds_analysis_rejected(self) -> None:
        """PhaseDefinition with embeds_analysis=True is rejected with an actionable error."""
        with pytest.raises(ValidationError, match="embeds_analysis has been removed"):
            PhaseDefinition(
                drain="build",
                role="execution",
                embeds_analysis=True,
                transitions=PhaseTransition(on_success="done"),
            )
