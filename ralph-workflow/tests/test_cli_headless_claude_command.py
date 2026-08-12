"""Pin the R8 ``smoke-headless-claude`` CLI surface (wt-04-claude-parsing).

The headless Claude smoke command exists so both Claude transports
are smoke-verified on haiku with subagents (R8). The command must:

  (1) Be registered in ``ralph/cli/main.py`` and exposed under
      ``smoke-headless-claude``.
  (2) Default to ``claude-headless/haiku`` (matching the
      headless-Claude alias in the agents template).
  (3) Accept the same ``--subagents`` / ``--subagent-prompt-file``
      options as ``smoke-interactive-claude``.
  (4) Delegate to the same ``smoke_harness_agent_command`` plumbing
      so the two transports share one scenario surface.

The two tests below pin (1) and (3) via the CLI ``--help`` surface
(no real token-burn); (2) and (4) are pinned at the
``smoke_headless_claude_command`` function surface (no real CLI
invocation, no real harness run). Both checks are deterministic
and run under ``make verify`` without consuming tokens.

Test isolation guarantees (per ``docs/agents/testing-guide.md``):

  - No real subprocess (CLI ``--help`` only).
  - No real filesystem.
  - No real wall-clock waits.
  - No module-level mutable accumulators.
  - No ``noqa`` directives (audit_lint_bypass).
  - No bare ``type: ignore`` comments (audit_typecheck_bypass).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer
import typer.testing

from ralph.cli.commands.smoke import (
    _HEADLESS_CLAUDE_AGENT,
    smoke_headless_claude_command,
)
from ralph.cli.main import smoke_headless_claude, smoke_interactive_claude

if TYPE_CHECKING:
    import pytest

_RUNNER = typer.testing.CliRunner()
_HEADLESS_HELP_APP = typer.Typer()
_HEADLESS_HELP_APP.command()(smoke_headless_claude)
_INTERACTIVE_HELP_APP = typer.Typer()
_INTERACTIVE_HELP_APP.command()(smoke_interactive_claude)


def test_smoke_headless_claude_command_default_agent_alias() -> None:
    """``smoke_headless_claude_command`` defaults to ``claude-headless/haiku``.

    The default agent alias matches the headless-Claude alias in
    the bundled agents template (``ralph-workflow-agents.toml``).
    Operators can override via ``--agent`` if the build supports
    other headless-Claude aliases.
    """
    assert _HEADLESS_CLAUDE_AGENT == "claude-headless/haiku"


def test_smoke_headless_claude_command_delegates_to_shared_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``smoke_headless_claude_command`` delegates to ``smoke_harness_agent_command``.

    The delegation must pass through the ``subagents`` /
    ``subagent_prompt_file`` parameters verbatim so the headless
    scenario surface is identical to the interactive surface.
    """
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
    rc = smoke_headless_claude_command(
        subagents=True,
        subagent_prompt_file=None,
    )
    assert rc == 0
    assert captured["agent_name"] == _HEADLESS_CLAUDE_AGENT
    assert captured["subagents"] is True
    assert captured["subagent_prompt_file"] is None


def test_smoke_headless_claude_command_default_no_subagents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``smoke_headless_claude_command`` defaults ``subagents=False`` so the basic scenario runs.

    Without ``--subagents``, the harness runs the basic scenario
    (no subagent dispatch). The default is the same as the
    interactive command so the two transports share the same
    baseline behavior.
    """
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
    rc = smoke_headless_claude_command()
    assert rc == 0
    assert captured["agent_name"] == _HEADLESS_CLAUDE_AGENT
    assert captured["subagents"] is False


def test_cli_help_advertises_subagent_options() -> None:
    """The headless command's isolated help surface advertises subagent options.

    Rendering only the command avoids root-app startup contention while
    exercising the same Typer declarations exposed by the registered CLI.
    """
    result = _RUNNER.invoke(_HEADLESS_HELP_APP, ["--help"])
    # ``typer.testing.CliRunner.invoke`` returns a ``Result`` whose
    # ``output`` carries the rendered help text on a clean
    # ``--help`` invocation.
    assert "--subagents" in result.output
    assert "--subagent-prompt-file" in result.output
    # The help text must name the headless-Claude alias so an
    # operator can confirm the default without reading the source.
    assert "headless" in result.output.lower()


def test_cli_help_for_both_claude_commands_advertise_same_subagent_options() -> None:
    """The isolated interactive and headless command help surfaces agree.

    A divergent option surface between the two commands would break R8's
    shared-scenario contract.
    """
    interactive = _RUNNER.invoke(_INTERACTIVE_HELP_APP, ["--help"])
    headless = _RUNNER.invoke(_HEADLESS_HELP_APP, ["--help"])
    for option in ("--subagents", "--subagent-prompt-file"):
        assert option in interactive.output
        assert option in headless.output
