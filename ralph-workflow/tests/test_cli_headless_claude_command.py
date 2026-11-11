from __future__ import annotations

import pytest
import typer
import typer.testing

from ralph.cli.commands.smoke import smoke_headless_claude_command
from ralph.cli.main import smoke_headless_claude, smoke_interactive_claude
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy

_RUNNER = typer.testing.CliRunner()
_HEADLESS_HELP_APP = typer.Typer()
_HEADLESS_HELP_APP.command()(smoke_headless_claude)
_INTERACTIVE_HELP_APP = typer.Typer()
_INTERACTIVE_HELP_APP.command()(smoke_interactive_claude)


def _headless_smoke_policy() -> AgentsPolicy:
    return AgentsPolicy(
        agent_chains={"smoke-development": AgentChainConfig(agents=["claude-headless"])},
        agent_drains={"development": AgentDrainConfig(chain="smoke-development")},
    )


def _resolved_default_agent() -> str:
    captured: dict[str, object] = {}

    def _stub(agent_name: str, **_kwargs: object) -> int:
        captured["agent_name"] = agent_name
        return 0

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("ralph.cli.commands.smoke.smoke_harness_agent_command", _stub)
        patch.setattr(
            "ralph.cli.commands.smoke.load_agents_policy_for_workspace_scope",
            lambda *_args, **_kwargs: _headless_smoke_policy(),
        )
        smoke_headless_claude_command()
    return str(captured["agent_name"])


def test_smoke_headless_claude_command_default_agent_alias_regression_is_not_pinned() -> None:
    resolved = _resolved_default_agent()

    assert resolved == "claude-headless" or resolved.startswith("claude-headless/")


def test_smoke_headless_claude_command_delegates_to_shared_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _stub_harness(
        agent_name: str,
        *,
        display_context: object | None = None,
        pro_hooks: object | None = None,
        model_identity: object | None = None,
        subagents: bool = False,
        subagent_prompt_file: object | None = None,
        multimodal: bool = False,
    ) -> int:
        captured["agent_name"] = agent_name
        captured["subagents"] = subagents
        captured["subagent_prompt_file"] = subagent_prompt_file
        captured["multimodal"] = multimodal
        return 0

    monkeypatch.setattr(
        "ralph.cli.commands.smoke.smoke_harness_agent_command",
        _stub_harness,
    )
    monkeypatch.setattr(
        "ralph.cli.commands.smoke.load_agents_policy_for_workspace_scope",
        lambda *_args, **_kwargs: _headless_smoke_policy(),
    )
    rc = smoke_headless_claude_command(
        subagents=True,
        subagent_prompt_file=None,
    )
    assert rc == 0
    assert captured["agent_name"] == _resolved_default_agent()
    assert captured["subagents"] is True
    assert captured["subagent_prompt_file"] is None


def test_smoke_headless_claude_command_default_no_subagents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _stub_harness(
        agent_name: str,
        *,
        display_context: object | None = None,
        pro_hooks: object | None = None,
        model_identity: object | None = None,
        subagents: bool = False,
        subagent_prompt_file: object | None = None,
        multimodal: bool = False,
    ) -> int:
        captured["agent_name"] = agent_name
        captured["subagents"] = subagents
        captured["multimodal"] = multimodal
        return 0

    monkeypatch.setattr(
        "ralph.cli.commands.smoke.smoke_harness_agent_command",
        _stub_harness,
    )
    monkeypatch.setattr(
        "ralph.cli.commands.smoke.load_agents_policy_for_workspace_scope",
        lambda *_args, **_kwargs: _headless_smoke_policy(),
    )
    rc = smoke_headless_claude_command()
    assert rc == 0
    assert captured["agent_name"] == _resolved_default_agent()
    assert captured["subagents"] is False


def test_cli_help_advertises_subagent_options() -> None:
    result = _RUNNER.invoke(_HEADLESS_HELP_APP, ["--help"])
    assert "--subagents" in result.output
    assert "--subagent-prompt-file" in result.output
    assert "headless" in result.output.lower()


def test_cli_help_for_both_claude_commands_advertise_same_subagent_options() -> None:
    interactive = _RUNNER.invoke(_INTERACTIVE_HELP_APP, ["--help"])
    headless = _RUNNER.invoke(_HEADLESS_HELP_APP, ["--help"])
    for option in ("--subagents", "--subagent-prompt-file"):
        assert option in interactive.output
        assert option in headless.output
