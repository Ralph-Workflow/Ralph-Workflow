from __future__ import annotations

from pathlib import Path

import pytest

from ralph.agents.registry import AgentRegistry
from ralph.cli.commands import run as run_module
from ralph.cli.commands.run import RunPipelineRequest
from ralph.config.models import UnifiedConfig
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
)
from ralph.policy.validation import (
    CheckpointPolicyMismatchError,
    PolicyValidationError,
    validate_agent_chains_satisfiable,
    validate_checkpoint_against_policy,
    validate_recovery_config,
)
from ralph.workspace.scope import WorkspaceScope
from tests.recovery._preflight_validation_helper__fakebundle import _FakeBundle
from tests.recovery._preflight_validation_helper__fakechainconfig import _FakeChainConfig


class _FakeAgentRegistry:
    def __init__(self, known_agents: set[str]) -> None:
        self._known = known_agents

    def get(self, name: str) -> object | None:
        return object() if name in self._known else None


def test_validate_agent_chains_satisfiable_passes_with_known_agents() -> None:
    bundle = _FakeBundle(
        chains={"main": _FakeChainConfig(agents=["claude"])},
        drains={},
        phases={},
    )
    registry = _FakeAgentRegistry(known_agents={"claude"})
    validate_agent_chains_satisfiable(bundle, registry)


def test_validate_agent_chains_satisfiable_fails_with_unknown_agent() -> None:
    bundle = _FakeBundle(
        chains={"main": _FakeChainConfig(agents=["nonexistent-agent"])},
        drains={},
        phases={},
    )
    registry = _FakeAgentRegistry(known_agents={"claude"})

    with pytest.raises(PolicyValidationError, match="unknown agent"):
        validate_agent_chains_satisfiable(bundle, registry)


def test_validate_agent_chains_satisfiable_regression_names_available_agy_models() -> None:
    bundle = _FakeBundle(
        chains={"development": _FakeChainConfig(agents=["agy/gemini-3.7-flash-low"])},
        drains={},
        phases={},
    )

    with pytest.raises(PolicyValidationError) as exc_info:
        validate_agent_chains_satisfiable(bundle, _FakeAgentRegistry(known_agents={"agy"}))

    assert "gemini-3.6-flash-low" in str(exc_info.value)


def test_unknown_model_alias_error_lists_live_published_models_not_a_stale_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ralph.agents import registry as registry_module

    measured_stdout = "\n".join(
        (
            "gemini-3.8-flash-low\tGemini 3.8 Flash (Low)",
            "claude-sonnet-5-5-high\tClaude Sonnet 5.5 (High)",
        )
    )
    monkeypatch.setattr(registry_module, "_default_agy_models_probe", lambda: measured_stdout)
    bundle = _FakeBundle(
        chains={"development": _FakeChainConfig(agents=["agy/claude-sonnet-5-5-thinking"])},
        drains={},
        phases={},
    )

    with pytest.raises(PolicyValidationError) as exc_info:
        validate_agent_chains_satisfiable(bundle, AgentRegistry.from_config(UnifiedConfig()))

    message = str(exc_info.value)
    assert "Available AGY models: claude-sonnet-5-5-high, gemini-3.8-flash-low." in message
    assert "claude-opus-4-6-thinking" not in message


@pytest.mark.parametrize(
    "agent",
    [
        "pi/anthropic/claude-sonnet-4",
        "codex/gpt-6.1-sol[effort-low]",
        "codex/gpt-6.1-sol[effort=low]",
        "codex/provider/future-model[effort=future-tier]",
    ],
)
def test_validate_agent_chains_satisfiable_accepts_dynamic_aliases(agent: str) -> None:
    bundle = _FakeBundle(
        chains={"planning": _FakeChainConfig(agents=[agent])},
        drains={},
        phases={},
    )
    registry = AgentRegistry.from_config(UnifiedConfig())

    validate_agent_chains_satisfiable(bundle, registry)


def test_validate_recovery_config_passes_with_valid_config() -> None:
    bundle = _FakeBundle(
        chains={"main": _FakeChainConfig(agents=["claude"], max_retries=3)},
        drains={},
        phases={},
    )
    validate_recovery_config(bundle)


def test_validate_recovery_config_fails_with_negative_retries() -> None:
    bundle = _FakeBundle(
        chains={"main": _FakeChainConfig(agents=["claude"], max_retries=-1)},
        drains={},
        phases={},
    )

    with pytest.raises(PolicyValidationError, match="max_retries"):
        validate_recovery_config(bundle)


def test_validate_checkpoint_against_policy_passes_when_phase_exists() -> None:
    state = PipelineState(phase="planning")
    bundle = _FakeBundle(
        chains={},
        drains={},
        phases={"planning": object()},
    )

    validate_checkpoint_against_policy(state, bundle)


def test_validate_checkpoint_against_policy_fails_when_phase_missing() -> None:
    state = PipelineState(phase="planning")
    bundle = _FakeBundle(
        chains={},
        drains={},
        phases={"development": object()},
    )

    with pytest.raises(CheckpointPolicyMismatchError):
        validate_checkpoint_against_policy(state, bundle)


def test_validate_checkpoint_against_policy_rejects_obsolete_checkpoint_format() -> None:
    bundle = PolicyBundle(
        agents=AgentsPolicy(
            agent_chains={"main": AgentChainConfig(agents=["claude"])},
            agent_drains={
                "planning": AgentDrainConfig(chain="main", drain_class="planning"),
                "complete": AgentDrainConfig(chain="main", drain_class="planning"),
            },
        ),
        artifacts=ArtifactsPolicy(artifacts={}),
        pipeline=PipelinePolicy(
            blocks={},
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
                ),
            },
            entry_block="developer_iteration",
            entry_phase="planning",
            terminal_phase="complete",
        ),
    )
    state = PipelineState(phase="planning")

    with pytest.raises(PolicyValidationError, match="obsolete checkpoint"):
        validate_checkpoint_against_policy(state, bundle)


def test_validation_error_message_is_actionable() -> None:
    bundle = _FakeBundle(
        chains={"main": _FakeChainConfig(agents=["unknown-agent"])},
        drains={},
        phases={},
    )
    registry = _FakeAgentRegistry(known_agents={"claude"})

    with pytest.raises(PolicyValidationError) as exc_info:
        validate_agent_chains_satisfiable(bundle, registry)

    error_msg = str(exc_info.value)
    assert "unknown-agent" in error_msg
    assert "unknown agent" in error_msg.lower()


_EXIT_PREFLIGHT = 2


def _build_policy_bundle(
    *,
    chains: dict[str, list[str]],
    drains: dict[str, str],
    max_retries: int = 3,
    phases: dict[str, str] | None = None,
) -> PolicyBundle:
    phase_map = phases or {"development": "development", "complete": "complete"}
    return PolicyBundle(
        agents=AgentsPolicy(
            agent_chains={
                name: AgentChainConfig(
                    agents=agents,
                    max_retries=max_retries,
                    retry_delay_ms=0,
                )
                for name, agents in chains.items()
            },
            agent_drains={drain: AgentDrainConfig(chain=chain) for drain, chain in drains.items()},
        ),
        pipeline=PipelinePolicy(
            phases={
                phase: PhaseDefinition(
                    drain=drain,
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete" if phase == "complete" else None,
                    ),
                )
                for phase, drain in phase_map.items()
            },
            entry_phase=next(iter(phase_map)),
            terminal_phase="complete",
        ),
        artifacts=ArtifactsPolicy(artifacts={}),
    )


def _fail_if_runner_reached(*args: object, **kwargs: object) -> int:
    pytest.fail("preflight test reached the real pipeline runner")


def test_run_pipeline_returns_2_on_unknown_agent_in_chain(
    tmp_path: Path,
) -> None:
    workspace = Path(tmp_path)
    (workspace / "PROMPT.md").write_text("Test prompt\n")
    (workspace / ".agent").mkdir()
    scope = WorkspaceScope(workspace)

    config = UnifiedConfig(
        agent_chains={"development": ["nonexistent-agent"]},
        agent_drains={"development": "development"},
    )
    bundle = _build_policy_bundle(
        chains={"development": ["nonexistent-agent"]},
        drains={"development": "development"},
    )

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(run_module, "resolve_workspace_scope", lambda: scope)
        mp.setattr(run_module, "load_config", lambda *args, **kwargs: config)
        mp.setattr(run_module, "load_policy", lambda *args, **kwargs: bundle)
        mp.setattr(run_module.state, "run_func", _fail_if_runner_reached)
        assert run_module.run_pipeline() == _EXIT_PREFLIGHT


def test_run_pipeline_returns_2_on_checkpoint_phase_mismatch(
    tmp_path: Path,
) -> None:
    workspace = Path(tmp_path)

    prompt = workspace / "PROMPT.md"
    prompt.write_text("Test prompt\n")

    agent_dir = workspace / ".agent"
    agent_dir.mkdir()

    agents_toml = agent_dir / "agents.toml"
    agents_toml.write_text(
        "[agent_chains.development]\n"
        'agents = ["claude"]\n'
        "max_retries = 3\n"
        "\n"
        "[agent_drains.development]\n"
        'chain = "development"\n'
    )

    pipeline_toml = agent_dir / "pipeline.toml"
    pipeline_toml.write_text(
        'entry_phase = "development"\n'
        'terminal_phase = "complete"\n'
        "\n"
        "[phases.development]\n"
        'drain = "development"\n'
        'transitions.on_success = "complete"\n'
        "\n"
        "[phases.complete]\n"
        'drain = "complete"\n'
        'transitions.on_success = "complete"\n'
        'transitions.on_loopback = "complete"\n'
    )

    checkpoint_json = agent_dir / "checkpoint.json"
    checkpoint_json.write_text(
        '{"phase": "planning", "recovery_cycle_count": 2, '
        '"fallover_history": [], "last_failure_category": "agent", '
        '"last_connectivity_state": "online", "recovery_cycle_cap": 200, '
        '"last_retry_delay_ms": 0}'
    )

    with pytest.MonkeyPatch().context() as mp:
        mp.setenv("RALPH_CHECKPOINT_PATH", str(checkpoint_json))

        mp.chdir(workspace)
        assert run_module.run_pipeline(request=RunPipelineRequest(resume=True)) == _EXIT_PREFLIGHT


def test_run_pipeline_returns_2_on_negative_max_retries(
    tmp_path: Path,
) -> None:
    workspace = Path(tmp_path)
    (workspace / "PROMPT.md").write_text("Test prompt\n")
    (workspace / ".agent").mkdir()
    scope = WorkspaceScope(workspace)

    config = UnifiedConfig()
    bundle = _build_policy_bundle(
        chains={"development": ["claude"]},
        drains={"development": "development"},
    )

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(run_module, "resolve_workspace_scope", lambda: scope)
        mp.setattr(run_module, "load_config", lambda *args, **kwargs: config)
        mp.setattr(run_module, "load_policy", lambda *args, **kwargs: bundle)
        mp.setattr(
            run_module,
            "validate_recovery_config",
            lambda _loaded_bundle: (_ for _ in ()).throw(
                PolicyValidationError("max_retries must be >= 0")
            ),
        )
        mp.setattr(run_module.state, "run_func", _fail_if_runner_reached)
        assert run_module.run_pipeline() == _EXIT_PREFLIGHT
