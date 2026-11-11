from __future__ import annotations

import shutil
from inspect import signature
from pathlib import Path

import pytest
from typer.testing import CliRunner

import ralph.pipeline.plumbing.smoke_plumbing as smoke_plumbing_module
from ralph.agents import registry as registry_module
from ralph.cli import main as cli_main
from ralph.cli.commands import smoke as smoke_module
from ralph.cli.commands._smoke_ccs import smoke_interactive_ccs_command
from ralph.cli.main import app
from ralph.config import loader as loader_module
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.pipeline.plumbing.smoke_plumbing import resolve_smoke_harness_spec
from ralph.policy import loader as policy_loader_module
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy
from ralph.workspace import scope as scope_module
from ralph.workspace.scope import WorkspaceScope

pytestmark = pytest.mark.smoke

_RUNNER = CliRunner()


def test_ccs_smoke_default_is_not_pinned_in_cli_or_command() -> None:
    cli_default = signature(cli_main.smoke_interactive_ccs).parameters["agent"].default
    command_default = signature(smoke_interactive_ccs_command).parameters["agent_name"].default

    assert cli_default.default is None
    assert command_default is None


def test_ccs_smoke_regression_default_comes_from_development_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scope = WorkspaceScope(tmp_path)
    config = UnifiedConfig()
    agents_policy = AgentsPolicy(
        agent_chains={"smoke-development": AgentChainConfig(agents=["ccs/work"])},
        agent_drains={"development": AgentDrainConfig(chain="smoke-development")},
    )
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/ccs")
    monkeypatch.setattr(scope_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(loader_module, "load_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(
        policy_loader_module,
        "load_agents_policy_for_workspace_scope",
        lambda *_args, **_kwargs: agents_policy,
    )

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: UnifiedConfig) -> FakeRegistry:
            return cls()

        def get(self, name: str) -> AgentConfig | None:
            if name == "ccs/work":
                return AgentConfig(cmd="ccs work", transport=AgentTransport.CLAUDE)
            return None

    monkeypatch.setattr(registry_module, "AgentRegistry", FakeRegistry)
    captured: dict[str, object] = {}

    def fake_harness(agent_name: str, **_kwargs: object) -> int:
        captured["agent_name"] = agent_name
        return 0

    monkeypatch.setattr(smoke_module, "smoke_harness_agent_command", fake_harness)

    exit_code = smoke_module.smoke_interactive_ccs_command(display_context=None)

    assert exit_code == 0
    assert captured["agent_name"] == "ccs/work"


def test_ccs_smoke_regression_exits_when_policy_lacks_ccs_agent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scope = WorkspaceScope(tmp_path)
    config = UnifiedConfig()
    agents_policy = AgentsPolicy(
        agent_chains={"smoke-development": AgentChainConfig(agents=["claude/sonnet"])},
        agent_drains={"development": AgentDrainConfig(chain="smoke-development")},
    )
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/ccs")
    monkeypatch.setattr(scope_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(loader_module, "load_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(
        policy_loader_module,
        "load_agents_policy_for_workspace_scope",
        lambda *_args, **_kwargs: agents_policy,
    )

    exit_code = smoke_module.smoke_interactive_ccs_command(display_context=None)

    assert exit_code == 2


def test_resolve_smoke_harness_spec_ccs_glm() -> None:
    spec = resolve_smoke_harness_spec("ccs/glm")

    assert spec.agent_name == "ccs/glm"
    assert spec.relative_dir.as_posix() == "tmp/interactive-ccs-smoke/glm"
    assert spec.output_file.as_posix() == "tmp/interactive-ccs-smoke/glm/todo-list.js"
    assert spec.run_id == "interactive-ccs-smoke-glm"


def test_resolve_smoke_harness_spec_ccs_aliases_do_not_collide() -> None:
    glm_spec = resolve_smoke_harness_spec("ccs/glm")
    work_spec = resolve_smoke_harness_spec("ccs/work")

    assert glm_spec.relative_dir != work_spec.relative_dir
    assert glm_spec.output_file != work_spec.output_file
    assert glm_spec.run_id != work_spec.run_id


def test_resolve_smoke_harness_spec_ccs_mm_scoped_without_collision() -> None:
    spec = resolve_smoke_harness_spec("ccs/mm")

    assert spec.agent_name == "ccs/mm"
    assert spec.relative_dir.as_posix() == "tmp/interactive-ccs-smoke/mm"
    assert spec.output_file.as_posix() == "tmp/interactive-ccs-smoke/mm/todo-list.js"
    assert spec.run_id == "interactive-ccs-smoke-mm"

    glm_spec = resolve_smoke_harness_spec("ccs/glm")
    work_spec = resolve_smoke_harness_spec("ccs/work")
    assert spec.relative_dir not in {glm_spec.relative_dir, work_spec.relative_dir}
    assert spec.output_file not in {glm_spec.output_file, work_spec.output_file}
    assert spec.run_id not in {glm_spec.run_id, work_spec.run_id}


def test_resolve_smoke_harness_spec_ccs_empty_alias_raises() -> None:
    with pytest.raises(ValueError, match="No smoke harness spec defined"):
        resolve_smoke_harness_spec("ccs/")


def test_smoke_interactive_ccs_command_exits_when_ccs_binary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    exit_code = smoke_module.smoke_interactive_ccs_command(display_context=None)

    assert exit_code == 2


def test_smoke_interactive_ccs_command_exits_when_agent_resolves_to_wrong_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/ccs")

    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(scope_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(loader_module, "load_config", lambda *_a, **_k: UnifiedConfig())

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: UnifiedConfig) -> FakeRegistry:
            return cls()

        def get(self, _name: str) -> AgentConfig | None:
            return AgentConfig(
                cmd="claude",
                transport=AgentTransport.CLAUDE_INTERACTIVE,
            )

    monkeypatch.setattr(registry_module, "AgentRegistry", FakeRegistry)

    exit_code = smoke_module.smoke_interactive_ccs_command(
        agent_name="claude/haiku",
        display_context=None,
    )

    assert exit_code == 2


def test_smoke_interactive_ccs_command_runs_harness_when_binary_present_and_alias_resolves(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/ccs")

    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(scope_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(loader_module, "load_config", lambda *_a, **_k: UnifiedConfig())

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: UnifiedConfig) -> FakeRegistry:
            return cls()

        def get(self, name: str) -> AgentConfig | None:
            if name == "ccs/glm":
                return AgentConfig(
                    cmd="ccs glm",
                    transport=AgentTransport.CLAUDE,
                )
            return None

    monkeypatch.setattr(registry_module, "AgentRegistry", FakeRegistry)

    captured: dict[str, object] = {}

    def fake_harness(agent_name: str, **kwargs: object) -> int:
        captured["agent_name"] = agent_name
        captured["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(smoke_module, "smoke_harness_agent_command", fake_harness)

    exit_code = smoke_module.smoke_interactive_ccs_command(
        agent_name="ccs/glm",
        display_context=None,
    )

    assert exit_code == 0
    assert captured["agent_name"] == "ccs/glm"


def test_smoke_interactive_ccs_command_runs_harness_for_multi_word_alias(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/ccs")

    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(scope_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(loader_module, "load_config", lambda *_a, **_k: UnifiedConfig())

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: UnifiedConfig) -> FakeRegistry:
            return cls()

        def get(self, name: str) -> AgentConfig | None:
            if name == "ccs/mm":
                return AgentConfig(
                    cmd="ccs mm",
                    transport=AgentTransport.CLAUDE,
                )
            return None

    monkeypatch.setattr(registry_module, "AgentRegistry", FakeRegistry)

    captured: dict[str, object] = {}

    def fake_harness(agent_name: str, **kwargs: object) -> int:
        captured["agent_name"] = agent_name
        captured["kwargs"] = kwargs
        return 0

    monkeypatch.setattr(smoke_module, "smoke_harness_agent_command", fake_harness)

    exit_code = smoke_module.smoke_interactive_ccs_command(
        agent_name="ccs/mm",
        display_context=None,
    )

    assert exit_code == 0
    assert captured["agent_name"] == "ccs/mm"


def test_cli_help_advertises_ccs_smoke_options() -> None:
    result = _RUNNER.invoke(app, ["smoke-interactive-ccs", "--help"])
    assert result.exit_code == 0
    output = result.stdout
    assert "--agent" in output
    assert "--subagents" in output
    assert "--subagent-prompt-file" in output


def test_ccs_mm_uses_headless_visible_output_ceiling() -> None:
    config = AgentConfig(
        cmd="ccs mm --output-format=stream-json --print --include-partial-messages",
        transport=AgentTransport.CLAUDE,
    )
    prefix = smoke_plumbing_module._resolve_visible_output_agent_prefix(config)
    assert prefix == "claude-headless"


def test_ccs_mm_subagent_visible_line_count_does_not_overrun() -> None:
    config = AgentConfig(
        cmd="ccs mm --output-format=stream-json --print --include-partial-messages",
        transport=AgentTransport.CLAUDE,
    )
    error = smoke_plumbing_module._visible_output_overrun_error(config, 82)
    assert error is None
