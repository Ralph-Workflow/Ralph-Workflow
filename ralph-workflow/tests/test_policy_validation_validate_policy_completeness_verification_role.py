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


class TestValidatePolicyCompletenessVerificationRole:
    """Tests for validation of role='verification' phases in validate_policy_completeness."""

    def _agents(self, drains: list[str]) -> AgentsPolicy:
        chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
        agent_drains = cast(
            "dict[DrainName, AgentDrainConfig]",
            {d: AgentDrainConfig(chain=d) for d in drains},
        )
        return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)

    def _terminal_phase(self) -> PhaseDefinition:
        return PhaseDefinition(
            drain="complete",
            role="terminal",
            terminal_outcome="success",
            transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
        )

    def test_verification_role_requires_verification_block(self) -> None:
        """role='verification' with no verification block fails completeness check."""
        agents = self._agents(["verify", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "verify": PhaseDefinition(
                    drain="verify",
                    role="verification",
                    transitions=PhaseTransition(on_success="complete"),
                    # verification intentionally absent
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="verify",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
        with pytest.raises(PolicyValidationError, match="requires a verification block"):
            validate_policy_completeness(bundle)

    def test_verification_kind_pydantic_rejects_invalid(self) -> None:
        """PhaseVerificationPolicy rejects invalid kind values."""
        with pytest.raises(ValidationError):
            PhaseVerificationPolicy(**{"kind": "bogus", "gate_for": "advancement"})

    def test_verification_gate_for_pydantic_rejects_invalid(self) -> None:
        """PhaseVerificationPolicy rejects invalid gate_for values."""
        with pytest.raises(ValidationError):
            PhaseVerificationPolicy(**{"kind": "none", "gate_for": "unknown_gate"})

    def test_verification_on_failure_route_unknown_phase_rejected(self) -> None:
        """on_failure_route naming a missing phase fails completeness check."""
        agents = self._agents(["verify", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "verify": PhaseDefinition(
                    drain="verify",
                    role="verification",
                    verification=PhaseVerificationPolicy(
                        kind="artifact",
                        gate_for="advancement",
                        on_failure_route="nonexistent_phase",
                    ),
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="verify",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
        with pytest.raises(PolicyValidationError, match="nonexistent_phase"):
            validate_policy_completeness(bundle)

    def test_verification_on_failure_route_failed_undeclared_rejected(self) -> None:
        """on_failure_route to undeclared 'failed' pseudo-phase is now rejected.

        The bare 'failed' alias is no longer a valid pseudo-phase target.
        Declare a phase with role='terminal' and terminal_outcome='failure' and
        reference it via on_failure_route.
        """
        agents = self._agents(["verify", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "verify": PhaseDefinition(
                    drain="verify",
                    role="verification",
                    verification=PhaseVerificationPolicy(
                        kind="none",
                        gate_for="advancement",
                        on_failure_route="failed",
                    ),
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="verify",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
        with pytest.raises(PolicyValidationError, match="'failed' is not a declared phase"):
            validate_policy_completeness(bundle)

    def test_verification_on_failure_route_legacy_pseudo_rejected(self) -> None:
        """on_failure_route to 'phase_failed' or 'exit_failure' pseudo-phases is rejected."""
        for pseudo in ("phase_failed", "exit_failure"):
            agents = self._agents(["verify", "complete"])
            pipeline = PipelinePolicy(
                phases={
                    "verify": PhaseDefinition(
                        drain="verify",
                        role="verification",
                        verification=PhaseVerificationPolicy(
                            kind="none",
                            gate_for="advancement",
                            on_failure_route=pseudo,
                        ),
                        transitions=PhaseTransition(on_success="complete"),
                    ),
                    "complete": self._terminal_phase(),
                },
                entry_phase="verify",
                terminal_phase="complete",
                recovery=RecoveryPolicy(failed_route="complete"),
            )
            bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
            with pytest.raises(PolicyValidationError, match=pseudo):
                validate_policy_completeness(bundle)

    def test_verification_on_failure_route_declared_terminal_phase_accepted(self) -> None:
        """on_failure_route pointing to a declared terminal phase passes."""
        agents = self._agents(["verify", "crashed", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "verify": PhaseDefinition(
                    drain="verify",
                    role="verification",
                    verification=PhaseVerificationPolicy(
                        kind="artifact",
                        gate_for="advancement",
                        on_failure_route="crashed",
                    ),
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "crashed": PhaseDefinition(
                    drain="crashed",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="crashed", on_loopback="crashed"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="verify",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="crashed"),
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
        validate_policy_completeness(bundle)  # must not raise

    def test_verification_with_valid_block_passes(self) -> None:
        """role='verification' with valid block passes completeness check."""
        agents = self._agents(["verify", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "verify": PhaseDefinition(
                    drain="verify",
                    role="verification",
                    verification=PhaseVerificationPolicy(
                        kind="none",
                        gate_for="advancement",
                        on_failure_route=None,
                    ),
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="verify",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
        validate_policy_completeness(bundle)  # must not raise
