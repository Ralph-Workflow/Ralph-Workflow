"""Unit tests for policy validation.

Tests cover:
- Valid agents.toml loading
- Missing drain binding raises PolicyValidationError
- Chain referencing unknown agent raises PolicyValidationError
- Empty chain list raises PolicyValidationError
- All six built-in drains are bound in default agents.toml
"""

from __future__ import annotations

import importlib
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
    PhaseVerificationPolicy,
    PipelinePolicy,
    PolicyBundle,
    RecoveryPolicy,
)
from ralph.policy.validation import (
    PolicyValidationError,
    validate_policy_completeness,
)

ValidationError = importlib.import_module("pydantic").ValidationError
DEFAULT_MAX_WORK_UNITS = 50


class TestValidateTerminalFailurePhaseRequired:
    """Tests for _validate_terminal_failure_phase_declared.

    When any phase declares on_failure or verification.on_failure_route transitions,
    at least one phase with role='terminal' and terminal_outcome='failure' must exist
    so the runtime has a policy-declared failure destination.
    """

    def _agents(self, drains: list[str]) -> AgentsPolicy:
        chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
        agent_drains = cast(
            "dict[DrainName, AgentDrainConfig]",
            {d: AgentDrainConfig(chain=d) for d in drains},
        )
        return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)

    def _terminal_success(self) -> PhaseDefinition:
        return PhaseDefinition(
            drain="complete",
            role="terminal",
            terminal_outcome="success",
            transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
        )

    def _terminal_failure(self, drain: str = "crashed") -> PhaseDefinition:
        return PhaseDefinition(
            drain=drain,
            role="terminal",
            terminal_outcome="failure",
            transitions=PhaseTransition(on_success=drain, on_loopback=drain),
        )

    def test_on_failure_without_terminal_failure_phase_raises(self) -> None:
        """Policy with on_failure route but no terminal-failure phase fails validation."""
        agents = self._agents(["work", "fallback", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="execution",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure="fallback",
                    ),
                ),
                "fallback": PhaseDefinition(
                    drain="fallback",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_success(),
            },
            entry_phase="work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle)
        assert "terminal_outcome='failure'" in str(exc_info.value)

    def test_verification_failure_route_without_terminal_failure_phase_raises(self) -> None:
        """Policy with verification.on_failure_route but no terminal-failure phase fails."""
        agents = self._agents(["work", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="verification",
                    verification=PhaseVerificationPolicy(
                        kind="artifact",
                        gate_for="advancement",
                        on_failure_route="complete",
                    ),
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_success(),
            },
            entry_phase="work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle)
        assert "terminal_outcome='failure'" in str(exc_info.value)

    def test_on_failure_with_terminal_failure_phase_passes(self) -> None:
        """Policy with on_failure route AND terminal-failure phase passes validation."""
        agents = self._agents(["work", "fallback", "complete", "crashed"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="execution",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure="fallback",
                    ),
                ),
                "fallback": PhaseDefinition(
                    drain="fallback",
                    role="execution",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure="crashed",
                    ),
                ),
                "complete": self._terminal_success(),
                "crashed": self._terminal_failure(),
            },
            entry_phase="work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="crashed"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)  # must not raise

    def test_no_failure_routes_skips_terminal_failure_check(self) -> None:
        """Policy with no on_failure routes does not require a terminal-failure phase."""
        agents = self._agents(["work", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_success(),
            },
            entry_phase="work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)  # must not raise
