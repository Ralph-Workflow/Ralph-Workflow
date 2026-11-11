from __future__ import annotations

import pytest
from typer.testing import CliRunner

from ralph.cli.commands import smoke as smoke_module
from ralph.cli.main import app

pytestmark = pytest.mark.smoke


_RUNNER = CliRunner()


def test_smoke_headless_claude_command_default_agent_alias() -> None:
    captured: dict[str, object] = {}

    def _stub(agent_name: str, **_kwargs: object) -> int:
        captured["agent_name"] = agent_name
        return 0

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("ralph.cli.commands.smoke.smoke_harness_agent_command", _stub)
        smoke_module.smoke_headless_claude_command()

    resolved = str(captured["agent_name"])
    assert resolved == "claude-headless" or resolved.startswith("claude-headless/")


def test_smoke_headless_claude_command_delegates_to_shared_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_harness(
        agent_name: str,
        *,
        display_context: object,
        pro_hooks: object,
        model_identity: object,
        subagents: bool,
        subagent_prompt_file: object,
        multimodal: bool,
    ) -> int:
        captured["agent_name"] = agent_name
        captured["display_context"] = display_context
        captured["pro_hooks"] = pro_hooks
        captured["model_identity"] = model_identity
        captured["subagents"] = subagents
        captured["subagent_prompt_file"] = subagent_prompt_file
        captured["multimodal"] = multimodal
        return 0

    monkeypatch.setattr(smoke_module, "smoke_harness_agent_command", fake_harness)

    sentinel_context = object()
    sentinel_hooks = object()
    sentinel_identity = object()
    sentinel_prompt = object()

    exit_code = smoke_module.smoke_headless_claude_command(
        display_context=sentinel_context,
        pro_hooks=sentinel_hooks,
        model_identity=sentinel_identity,
        subagents=True,
        subagent_prompt_file=sentinel_prompt,
        multimodal=True,
    )

    assert exit_code == 0
    resolved = str(captured["agent_name"])
    assert resolved == "claude-headless" or resolved.startswith("claude-headless/")
    assert captured["display_context"] is sentinel_context
    assert captured["pro_hooks"] is sentinel_hooks
    assert captured["model_identity"] is sentinel_identity
    assert captured["subagents"] is True
    assert captured["subagent_prompt_file"] is sentinel_prompt
    assert captured["multimodal"] is True


def test_smoke_headless_claude_command_default_no_subagents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_harness(
        agent_name: str,
        *,
        display_context: object,
        pro_hooks: object,
        model_identity: object,
        subagents: bool,
        subagent_prompt_file: object,
        multimodal: bool,
    ) -> int:
        captured["subagents"] = subagents
        captured["subagent_prompt_file"] = subagent_prompt_file
        captured["multimodal"] = multimodal
        return 0

    monkeypatch.setattr(smoke_module, "smoke_harness_agent_command", fake_harness)

    exit_code = smoke_module.smoke_headless_claude_command(display_context=None)

    assert exit_code == 0
    assert captured["subagents"] is False
    assert captured["subagent_prompt_file"] is None
    assert captured["multimodal"] is False


def test_cli_help_advertises_subagent_options() -> None:
    result = _RUNNER.invoke(app, ["smoke-headless-claude", "--help"])
    assert result.exit_code == 0
    output = result.stdout
    assert "--subagents" in output
    assert "--subagent-prompt-file" in output


def test_cli_help_for_both_claude_commands_advertise_same_subagent_options() -> None:
    interactive_help = _RUNNER.invoke(app, ["smoke-interactive-claude", "--help"])
    headless_help = _RUNNER.invoke(app, ["smoke-headless-claude", "--help"])
    assert interactive_help.exit_code == 0
    assert headless_help.exit_code == 0

    for option in ("--subagents", "--subagent-prompt-file"):
        assert option in interactive_help.stdout, (
            f"smoke-interactive-claude --help is missing {option}"
        )
        assert option in headless_help.stdout, f"smoke-headless-claude --help is missing {option}"
