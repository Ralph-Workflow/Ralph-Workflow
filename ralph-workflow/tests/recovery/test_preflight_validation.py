"""Black-box test: pre-flight validation catches configuration errors at startup."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.cli.commands import run as run_module
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
    validate_agent_chains_satisfiable(bundle, registry)  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library


def test_validate_agent_chains_satisfiable_fails_with_unknown_agent() -> None:
    """Validation raises PolicyValidationError when chain references unknown agent."""
    bundle = _FakeBundle(
        chains={"main": _FakeChainConfig(agents=["nonexistent-agent"])},
        drains={},
        phases={},
    )
    registry = _FakeAgentRegistry(known_agents={"claude"})

    with pytest.raises(PolicyValidationError, match="unknown agent"):
        validate_agent_chains_satisfiable(bundle, registry)  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library


def test_validate_recovery_config_passes_with_valid_config() -> None:
    """Validation passes when all chains have max_retries >= 0."""
    bundle = _FakeBundle(
        chains={"main": _FakeChainConfig(agents=["claude"], max_retries=3)},
        drains={},
        phases={},
    )
    # Should not raise
    validate_recovery_config(bundle)  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library


def test_validate_recovery_config_fails_with_negative_retries() -> None:
    """Validation raises PolicyValidationError when max_retries < 0."""
    bundle = _FakeBundle(
        chains={"main": _FakeChainConfig(agents=["claude"], max_retries=-1)},
        drains={},
        phases={},
    )

    with pytest.raises(PolicyValidationError, match="max_retries"):
        validate_recovery_config(bundle)  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library


def test_validate_checkpoint_against_policy_passes_when_phase_exists() -> None:
    """Checkpoint validation passes when phase is in the current policy."""
    state = PipelineState(phase="planning")  # type: ignore[call-arg]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    bundle = _FakeBundle(
        chains={},
        drains={},
        phases={"planning": object()},
    )

    # Should not raise
    validate_checkpoint_against_policy(state, bundle)  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library


def test_validate_checkpoint_against_policy_fails_when_phase_missing() -> None:
    """Checkpoint validation raises when phase is not in the current policy."""
    state = PipelineState(phase="planning")
    bundle = _FakeBundle(
        chains={},
        drains={},
        phases={"development": object()},  # planning is missing
    )

    with pytest.raises(CheckpointPolicyMismatchError):
        validate_checkpoint_against_policy(state, bundle)  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library


def test_validation_error_message_is_actionable() -> None:
    """Validation error messages should tell the user what to fix."""
    bundle = _FakeBundle(
        chains={"main": _FakeChainConfig(agents=["unknown-agent"])},
        drains={},
        phases={},
    )
    registry = _FakeAgentRegistry(known_agents={"claude"})

    with pytest.raises(PolicyValidationError) as exc_info:
        validate_agent_chains_satisfiable(bundle, registry)  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library

    error_msg = str(exc_info.value)
    # Should mention the unknown agent
    assert "unknown-agent" in error_msg
    # Should mention it references an unknown agent
    assert "unknown agent" in error_msg.lower()


# ---------------------------------------------------------------------------
# Run-command preflight integration tests
# ---------------------------------------------------------------------------

_EXIT_PREFLIGHT = 2

def test_run_pipeline_returns_2_on_unknown_agent_in_chain(
    tmp_path: Path,
) -> None:
    """run_pipeline returns 2 when an agent chain references an unknown agent.

    This verifies the preflight validation is wired into the run command path and
    stops before pipeline execution when agents.toml references an unregistered
    agent.
    """
    workspace = Path(tmp_path)

    # Create required PROMPT.md
    prompt = workspace / "PROMPT.md"
    prompt.write_text("Test prompt\n")

    # Create .agent directory with agents.toml referencing unknown agent
    agent_dir = workspace / ".agent"
    agent_dir.mkdir()

    agents_toml = agent_dir / "agents.toml"
    agents_toml.write_text(
        '[agent_chains.development]\n'
        'agents = ["nonexistent-agent"]\n'
        'max_retries = 3\n'
        '\n'
        '[agent_drains.development]\n'
        'chain = "development"\n'
    )

    pipeline_toml = agent_dir / "pipeline.toml"
    pipeline_toml.write_text(
        'entry_phase = "development"\n'
        'terminal_phase = "complete"\n'
        '\n'
        '[phases.development]\n'
        'drain = "development"\n'
        'transitions.on_success = "complete"\n'
        '\n'
        '[phases.complete]\n'
        'drain = "complete"\n'
        'transitions.on_success = "complete"\n'
        'transitions.on_loopback = "complete"\n'
    )

    # Invoke CLI from the workspace so policy preflight loads .agent/*
    with pytest.MonkeyPatch().context() as mp:
        mp.chdir(workspace)
        assert run_module.run_pipeline() == _EXIT_PREFLIGHT


def test_run_pipeline_returns_2_on_checkpoint_phase_mismatch(
    tmp_path: Path,
) -> None:
    """run_pipeline returns 2 when checkpoint phase is not in current policy.

    A checkpoint saved at phase 'planning' but the current policy only defines
    'development' should fail preflight before pipeline execution.
    """
    workspace = Path(tmp_path)

    # Create required PROMPT.md
    prompt = workspace / "PROMPT.md"
    prompt.write_text("Test prompt\n")

    # Create .agent directory with policy and checkpoint
    agent_dir = workspace / ".agent"
    agent_dir.mkdir()

    # Policy only defines 'development' phase (not 'planning')
    agents_toml = agent_dir / "agents.toml"
    agents_toml.write_text(
        '[agent_chains.development]\n'
        'agents = ["claude"]\n'
        'max_retries = 3\n'
        '\n'
        '[agent_drains.development]\n'
        'chain = "development"\n'
    )

    pipeline_toml = agent_dir / "pipeline.toml"
    pipeline_toml.write_text(
        'entry_phase = "development"\n'
        'terminal_phase = "complete"\n'
        '\n'
        '[phases.development]\n'
        'drain = "development"\n'
        'transitions.on_success = "complete"\n'
        '\n'
        '[phases.complete]\n'
        'drain = "complete"\n'
        'transitions.on_success = "complete"\n'
        'transitions.on_loopback = "complete"\n'
    )

    # Create checkpoint with phase 'planning' (not in current policy)
    checkpoint_json = agent_dir / "checkpoint.json"
    checkpoint_json.write_text(
        '{"phase": "planning", "recovery_cycle_count": 2, '
        '"fallover_history": [], "last_failure_category": "agent", '
        '"last_connectivity_state": "online", "recovery_cycle_cap": 200, '
        '"last_retry_delay_ms": 0}'
    )

    # Set environment variable to use our checkpoint
    with pytest.MonkeyPatch().context() as mp:
        mp.setenv("RALPH_CHECKPOINT_PATH", str(checkpoint_json))

        mp.chdir(workspace)
        assert run_module.run_pipeline(resume=True) == _EXIT_PREFLIGHT


def test_run_pipeline_returns_2_on_negative_max_retries(
    tmp_path: Path,
) -> None:
    """run_pipeline returns 2 when max_retries is negative in agents.toml."""
    workspace = Path(tmp_path)

    # Create required PROMPT.md
    prompt = workspace / "PROMPT.md"
    prompt.write_text("Test prompt\n")

    # Create .agent directory with broken agents.toml
    agent_dir = workspace / ".agent"
    agent_dir.mkdir()

    agents_toml = agent_dir / "agents.toml"
    agents_toml.write_text(
        '[agent_chains.development]\n'
        'agents = ["claude"]\n'
        'max_retries = -5\n'
        '\n'
        '[agent_drains.development]\n'
        'chain = "development"\n'
    )

    pipeline_toml = agent_dir / "pipeline.toml"
    pipeline_toml.write_text(
        'entry_phase = "development"\n'
        'terminal_phase = "complete"\n'
        '\n'
        '[phases.development]\n'
        'drain = "development"\n'
        'transitions.on_success = "complete"\n'
        '\n'
        '[phases.complete]\n'
        'drain = "complete"\n'
        'transitions.on_success = "complete"\n'
        'transitions.on_loopback = "complete"\n'
    )

    with pytest.MonkeyPatch().context() as mp:
        mp.chdir(workspace)
        assert run_module.run_pipeline() == _EXIT_PREFLIGHT
