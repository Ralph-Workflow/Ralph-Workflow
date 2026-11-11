from __future__ import annotations

import inspect
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.registry import AgentRegistry
from ralph.cli import main as cli_main
from ralph.cli.commands import smoke as smoke_module
from ralph.cli.commands.smoke_agent_defaults import (
    CONFIG_ALIAS_DEFAULT_SMOKE_COMMANDS,
    SMOKE_COMMAND_TRANSPORTS,
    resolve_default_smoke_agent,
)
from ralph.cli.commands.smoke_binary_override import (
    apply_smoke_binary_override,
    smoke_binary_override_env_var,
    smoke_binary_override_transports,
)
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.pipeline.plumbing.smoke_evidence import Evidence, Provenance
from ralph.pipeline.plumbing.smoke_plumbing import SmokeRunResult
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.deps import PipelineDeps


def _cli_agent_defaults() -> dict[str, object]:
    defaults: dict[str, object] = {}
    for command in cli_main.app.registered_commands:
        name = command.name
        callback: Callable[..., None] | None = command.callback
        if name is None or callback is None or not name.startswith("smoke-"):
            continue
        parameter = inspect.signature(callback).parameters.get("agent")
        if parameter is None:
            continue
        option: object = parameter.default
        defaults[name] = getattr(option, "default", option)
    return defaults


@pytest.mark.timeout_seconds(3)
def test_every_agent_taking_command_defers_its_default_to_the_config() -> None:
    offenders: list[str] = []
    for name, default in _cli_agent_defaults().items():
        if name in CONFIG_ALIAS_DEFAULT_SMOKE_COMMANDS:
            continue
        if name not in SMOKE_COMMAND_TRANSPORTS:
            offenders.append(f"{name}: not declared in SMOKE_COMMAND_TRANSPORTS")
            continue
        if default is not None:
            offenders.append(f"{name}: CLI default {default!r}")
    assert offenders == []


@pytest.mark.timeout_seconds(3)
def test_command_seam_default_matches_the_cli_default() -> None:
    offenders: list[str] = []
    for name in SMOKE_COMMAND_TRANSPORTS:
        command = getattr(smoke_module, name.replace("-", "_") + "_command")
        default = inspect.signature(command).parameters["agent_name"].default
        if default is not None:
            offenders.append(f"{name}: command default {default!r}")
    assert offenders == []


@pytest.mark.timeout_seconds(3)
def test_default_resolves_to_the_development_policy_chain_alias() -> None:
    config = UnifiedConfig(
        agent_chains={
            "development": ["claude/sonnet"],
        }
    )
    agents_policy = AgentsPolicy(
        agent_chains={
            "smoke-development": AgentChainConfig(
                agents=["pi/omnirouter/kmc/k3", "codex/gpt-5.6-terra"]
            )
        },
        agent_drains={"development": AgentDrainConfig(chain="smoke-development")},
    )
    lookup = AgentRegistry.from_config(config).get

    assert resolve_default_smoke_agent(
        AgentTransport.PI, agents_policy, lookup, drain="development"
    ) == ("pi/omnirouter/kmc/k3")
    assert resolve_default_smoke_agent(
        AgentTransport.CODEX, agents_policy, lookup, drain="development"
    ) == ("codex/gpt-5.6-terra")


@pytest.mark.timeout_seconds(3)
def test_default_uses_bare_transport_when_the_development_policy_chain_has_no_match() -> None:
    config = UnifiedConfig()
    agents_policy = AgentsPolicy(
        agent_chains={"smoke-development": AgentChainConfig(agents=["claude/sonnet"])},
        agent_drains={"development": AgentDrainConfig(chain="smoke-development")},
    )
    lookup = AgentRegistry.from_config(config).get

    assert (
        resolve_default_smoke_agent(
            AgentTransport.OPENCODE, agents_policy, lookup, drain="development"
        )
        == "opencode"
    )


@pytest.mark.timeout_seconds(3)
def test_opencode_smoke_uses_bare_agent_when_policy_lacks_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = UnifiedConfig()
    agents_policy = AgentsPolicy(
        agent_chains={"smoke-development": AgentChainConfig(agents=["claude/sonnet"])},
        agent_drains={"development": AgentDrainConfig(chain="smoke-development")},
    )
    monkeypatch.setattr(smoke_module.shutil, "which", lambda _name: "/usr/bin/opencode")
    monkeypatch.setattr(smoke_module, "load_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(
        smoke_module,
        "load_agents_policy_for_workspace_scope",
        lambda *_args, **_kwargs: agents_policy,
    )
    captured: dict[str, object] = {}

    def fake_harness(agent_name: str, **_kwargs: object) -> int:
        captured["agent_name"] = agent_name
        return 0

    monkeypatch.setattr(smoke_module, "smoke_harness_agent_command", fake_harness)

    exit_code = smoke_module.smoke_interactive_opencode_command(display_context=None)

    assert exit_code == 0
    assert captured["agent_name"] == "opencode"


@pytest.mark.timeout_seconds(3)
def test_command_without_agent_flag_runs_the_configured_chain_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = UnifiedConfig(agent_chains={"development": ["claude/sonnet"]})
    agents_policy = AgentsPolicy(
        agent_chains={"smoke-development": AgentChainConfig(agents=["cursor/gpt-5.3-codex-high"])},
        agent_drains={"development": AgentDrainConfig(chain="smoke-development")},
    )
    monkeypatch.setattr(smoke_module.shutil, "which", lambda _name: "/usr/bin/agent")
    monkeypatch.setattr(smoke_module, "load_config", lambda *_a, **_k: config)
    monkeypatch.setattr(
        smoke_module,
        "load_agents_policy_for_workspace_scope",
        lambda *_a, **_k: agents_policy,
    )
    captured: dict[str, object] = {}

    def fake_harness(agent_name: str, **_kwargs: object) -> int:
        captured["agent_name"] = agent_name
        return 0

    monkeypatch.setattr(smoke_module, "smoke_harness_agent_command", fake_harness)

    exit_code = smoke_module.smoke_interactive_cursor_command(display_context=None)

    assert exit_code == 0
    assert captured["agent_name"] == "cursor/gpt-5.3-codex-high"


@pytest.mark.timeout_seconds(3)
def test_explicit_agent_flag_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    config = UnifiedConfig(agent_chains={"development": ["cursor/auto"]})
    monkeypatch.setattr(smoke_module.shutil, "which", lambda _name: "/usr/bin/agent")
    monkeypatch.setattr(smoke_module, "load_config", lambda *_a, **_k: config)
    captured: dict[str, object] = {}

    def fake_harness(agent_name: str, **_kwargs: object) -> int:
        captured["agent_name"] = agent_name
        return 0

    monkeypatch.setattr(smoke_module, "smoke_harness_agent_command", fake_harness)

    exit_code = smoke_module.smoke_interactive_cursor_command(
        agent_name="cursor/gpt-5.3-codex-high",
        display_context=None,
    )

    assert exit_code == 0
    assert captured["agent_name"] == "cursor/gpt-5.3-codex-high"


def _executable_stub(tmp_path: Path, name: str) -> Path:
    stub = tmp_path / name
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    return stub


class _FakePipelineFactory:
    def build(self, *_args: object, **_kwargs: object) -> PipelineDeps | None:
        return None


@pytest.mark.timeout_seconds(3)
def test_override_table_covers_every_documented_env_var() -> None:
    assert {
        smoke_binary_override_env_var(transport) for transport in smoke_binary_override_transports()
    } == {
        "RALPH_AGY_BINARY",
        "RALPH_CURSOR_BINARY",
        "RALPH_KIMI_BINARY",
        "RALPH_OPENCODE_BINARY",
    }


@pytest.mark.timeout_seconds(3)
@pytest.mark.parametrize("transport", list(smoke_binary_override_transports()))
def test_shared_harness_applies_every_override_transport(
    transport: AgentTransport,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stub = _executable_stub(tmp_path, f"stub-{transport.value}")
    env_var = smoke_binary_override_env_var(transport)
    assert env_var is not None
    monkeypatch.setenv(env_var, str(stub))
    monkeypatch.setenv("RALPH_BROKER_SECRET", "0" * 64)
    agent_name = transport.value
    monkeypatch.setattr(smoke_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(smoke_module, "load_config", lambda *_a, **_k: UnifiedConfig())
    monkeypatch.setattr(smoke_module, "DefaultPipelineFactory", _FakePipelineFactory)
    captured: dict[str, object] = {}

    def fake_run_smoke_plumbing(**kwargs: object) -> SmokeRunResult:
        captured["config"] = kwargs["config"]
        return SmokeRunResult(
            agent_name=agent_name,
            transport=transport.value,
            output_file=tmp_path / "todo-list.js",
            file_created=True,
            session_id="sess-1",
            explicit_completion_seen=Evidence(True, Provenance.WIRE, "test fixture"),
            raw_line_count=1,
            parsed_event_count=1,
            tool_activity_seen=Evidence(True, Provenance.WIRE, "test fixture"),
            artifact_submitted=Evidence(True, Provenance.WIRE, "test fixture"),
            meaningful_output_lines=["ok"],
            errors=[],
        )

    monkeypatch.setattr(smoke_module, "run_smoke_plumbing", fake_run_smoke_plumbing)

    smoke_module.smoke_harness_agent_command(agent_name, display_context=None)

    effective = captured["config"]
    assert isinstance(effective, UnifiedConfig)
    applied = effective.agents.get(agent_name)
    assert applied is not None, f"{transport.value}: harness discarded the override"
    assert applied.cmd == shlex.quote(str(stub))


@pytest.mark.timeout_seconds(3)
def test_override_is_a_no_op_for_a_transport_without_one() -> None:
    agent_config = AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE)

    assert apply_smoke_binary_override(agent_config) is agent_config


@pytest.mark.timeout_seconds(3)
def test_every_smoke_command_that_drives_an_agent_accepts_an_agent_flag() -> None:
    agent_taking = set(_cli_agent_defaults())
    driving = {
        command.name
        for command in cli_main.app.registered_commands
        if command.name is not None
        and command.name.startswith("smoke-")
        and command.callback is not None
    }

    assert sorted(driving - agent_taking) == []
