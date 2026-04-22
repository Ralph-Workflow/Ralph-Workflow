"""Black-box test: pre-flight validation catches configuration errors at startup."""

from __future__ import annotations

import pytest

from ralph.pipeline.state import PipelineState
from ralph.policy.validation import (
    CheckpointPolicyMismatchError,
    PolicyValidationError,
    validate_agent_chains_satisfiable,
    validate_checkpoint_against_policy,
    validate_recovery_config,
)


class _FakeAgentRegistry:
    """Minimal fake registry for test injection."""

    def __init__(self, known_agents: set[str]) -> None:
        self._known = known_agents

    def get(self, name: str) -> object | None:
        return object() if name in self._known else None


class _FakeChainConfig:
    def __init__(self, agents: list[str], max_retries: int = 3) -> None:
        self.agents = agents
        self.max_retries = max_retries


class _FakeAgentsPolicy:
    def __init__(self, chains: dict[str, _FakeChainConfig], drains: dict[str, object]) -> None:
        self.agent_chains = chains
        self.agent_drains = drains


class _FakePipelinePolicy:
    def __init__(self, phases: dict[str, object]) -> None:
        self.phases = phases


class _FakeBundle:
    def __init__(
        self,
        chains: dict[str, _FakeChainConfig],
        drains: dict[str, object],
        phases: dict[str, object],
    ) -> None:
        self.agents = _FakeAgentsPolicy(chains, drains)
        self.pipeline = _FakePipelinePolicy(phases)


def test_validate_agent_chains_satisfiable_passes_with_known_agents() -> None:
    """Validation passes when all chain agents exist in the registry."""
    bundle = _FakeBundle(
        chains={"main": _FakeChainConfig(agents=["claude"])},
        drains={},
        phases={},
    )
    registry = _FakeAgentRegistry(known_agents={"claude"})
    # Should not raise
    validate_agent_chains_satisfiable(bundle, registry)  # type: ignore[arg-type]


def test_validate_agent_chains_satisfiable_fails_with_unknown_agent() -> None:
    """Validation raises PolicyValidationError when chain references unknown agent."""
    bundle = _FakeBundle(
        chains={"main": _FakeChainConfig(agents=["nonexistent-agent"])},
        drains={},
        phases={},
    )
    registry = _FakeAgentRegistry(known_agents={"claude"})

    with pytest.raises(PolicyValidationError, match="unknown agent"):
        validate_agent_chains_satisfiable(bundle, registry)  # type: ignore[arg-type]


def test_validate_recovery_config_passes_with_valid_config() -> None:
    """Validation passes when all chains have max_retries >= 0."""
    bundle = _FakeBundle(
        chains={"main": _FakeChainConfig(agents=["claude"], max_retries=3)},
        drains={},
        phases={},
    )
    # Should not raise
    validate_recovery_config(bundle)  # type: ignore[arg-type]


def test_validate_recovery_config_fails_with_negative_retries() -> None:
    """Validation raises PolicyValidationError when max_retries < 0."""
    bundle = _FakeBundle(
        chains={"main": _FakeChainConfig(agents=["claude"], max_retries=-1)},
        drains={},
        phases={},
    )

    with pytest.raises(PolicyValidationError, match="max_retries"):
        validate_recovery_config(bundle)  # type: ignore[arg-type]


def test_validate_checkpoint_against_policy_passes_when_phase_exists() -> None:
    """Checkpoint validation passes when phase is in the current policy."""
    state = PipelineState(phase="planning")  # type: ignore[call-arg]
    bundle = _FakeBundle(
        chains={},
        drains={},
        phases={"planning": object()},
    )

    # Should not raise
    validate_checkpoint_against_policy(state, bundle)  # type: ignore[arg-type]


def test_validate_checkpoint_against_policy_fails_when_phase_missing() -> None:
    """Checkpoint validation raises when phase is not in the current policy."""
    state = PipelineState(phase="planning")
    bundle = _FakeBundle(
        chains={},
        drains={},
        phases={"development": object()},  # planning is missing
    )

    with pytest.raises(CheckpointPolicyMismatchError):
        validate_checkpoint_against_policy(state, bundle)  # type: ignore[arg-type]
