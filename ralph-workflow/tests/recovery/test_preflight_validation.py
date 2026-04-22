"""Black-box test: pre-flight validation catches configuration errors at startup."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import typer.testing

from ralph.cli.commands.run import app as run_app
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


def test_validation_error_message_is_actionable() -> None:
    """Validation error messages should tell the user what to fix."""
    bundle = _FakeBundle(
        chains={"main": _FakeChainConfig(agents=["unknown-agent"])},
        drains={},
        phases={},
    )
    registry = _FakeAgentRegistry(known_agents={"claude"})

    with pytest.raises(PolicyValidationError) as exc_info:
        validate_agent_chains_satisfiable(bundle, registry)  # type: ignore[arg-type]

    error_msg = str(exc_info.value)
    # Should mention the unknown agent
    assert "unknown-agent" in error_msg
    # Should mention it references an unknown agent
    assert "unknown agent" in error_msg.lower()


# ---------------------------------------------------------------------------
# CLI-level integration tests using CliRunner
# ---------------------------------------------------------------------------

_runner = typer.testing.CliRunner()


def test_cli_run_exits_2_on_unknown_agent_in_chain(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """CLI run command exits with code 2 when agent chain references unknown agent.

    This is a black-box integration test that verifies the preflight validation
    is wired into the CLI entry point and produces the correct exit code.
    It creates a temporary workspace with a broken agents.toml and asserts that
    'ralph run' exits with code 2 before starting the pipeline.
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
        '[chains.development]\n'
        'agents = ["nonexistent-agent"]\n'
        'max_retries = 3\n'
        '\n'
        '[drains.development]\n'
        'chain = "development"\n'
    )

    pipeline_toml = agent_dir / "pipeline.toml"
    pipeline_toml.write_text(
        '[phases.development]\n'
        'drain = "development"\n'
        'transitions.on_success = "complete"\n'
    )

    # Invoke CLI run command
    result = _runner.invoke(
        run_app,
        ["--config", str(workspace / "ralph.toml")],
        catch_exceptions=False,
    )

    # The CLI should exit with code 2 due to preflight validation failure
    assert result.exit_code == 2
    # stderr/stdout should contain actionable message about unknown agent
    output = (result.stdout or "") + (result.stderr or "")
    assert "unknown" in output.lower() or "Preflight" in output


def test_cli_run_exits_2_on_checkpoint_phase_mismatch(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """CLI run exits with code 2 when checkpoint phase is not in current policy.

    A checkpoint saved at phase 'planning' but the current policy only defines
    'development' should exit with code 2 preflight error.
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
        '[chains.development]\n'
        'agents = ["claude"]\n'
        'max_retries = 3\n'
        '\n'
        '[drains.development]\n'
        'chain = "development"\n'
    )

    pipeline_toml = agent_dir / "pipeline.toml"
    pipeline_toml.write_text(
        '[phases.development]\n'
        'drain = "development"\n'
        'transitions.on_success = "complete"\n'
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

        result = _runner.invoke(
            run_app,
            ["--config", str(workspace / "ralph.toml"), "--resume"],
            catch_exceptions=False,
        )

    # Should exit with code 2 due to checkpoint/policy mismatch
    assert result.exit_code == 2
    output = (result.stdout or "") + (result.stderr or "")
    assert "checkpoint" in output.lower() or "phase" in output.lower()


def test_cli_run_exits_2_on_negative_max_retries(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """CLI run command exits with code 2 when max_retries is negative in agents.toml."""
    workspace = Path(tmp_path)

    # Create required PROMPT.md
    prompt = workspace / "PROMPT.md"
    prompt.write_text("Test prompt\n")

    # Create .agent directory with broken agents.toml
    agent_dir = workspace / ".agent"
    agent_dir.mkdir()

    agents_toml = agent_dir / "agents.toml"
    agents_toml.write_text(
        '[chains.development]\n'
        'agents = ["claude"]\n'
        'max_retries = -5\n'
        '\n'
        '[drains.development]\n'
        'chain = "development"\n'
    )

    pipeline_toml = agent_dir / "pipeline.toml"
    pipeline_toml.write_text(
        '[phases.development]\n'
        'drain = "development"\n'
        'transitions.on_success = "complete"\n'
    )

    result = _runner.invoke(
        run_app,
        ["--config", str(workspace / "ralph.toml")],
        catch_exceptions=False,
    )

    assert result.exit_code == 2
    output = (result.stdout or "") + (result.stderr or "")
    assert "max_retries" in output.lower() or "Preflight" in output
