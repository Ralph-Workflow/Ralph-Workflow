"""Pin the headless-Claude smoke command surface (S-7 / R8 of wt-04).

The headless-Claude smoke command ``ralph smoke-headless-claude`` is
required by the plan (S-7) and the product criteria (R8) so the
two Claude transports are checked by one shared scenario rather
than two divergent ones. The command is registered in
``ralph/cli/main.py`` and delegates to ``smoke_harness_agent_command``
via ``smoke_headless_claude_command`` (``ralph/cli/commands/smoke.py``).

The tests below pin five contract points (one per acceptance
criterion in the plan / development-result proof):

  1. ``test_smoke_headless_claude_command_default_agent_alias`` --
     the default ``agent_name`` for ``smoke_headless_claude_command``
     is exactly ``claude-headless/haiku`` (matches the
     interactive ``claude/haiku`` alias shape).
  2. ``test_smoke_headless_claude_command_delegates_to_shared_harness`` --
     ``smoke_headless_claude_command`` is a thin pass-through to
     ``smoke_harness_agent_command`` with the same arguments
     (``display_context``, ``pro_hooks``, ``model_identity``,
     ``subagents``, ``subagent_prompt_file``).
  3. ``test_smoke_headless_claude_command_default_no_subagents`` --
     the default ``subagents`` flag is ``False`` so a no-subagent
     invocation does not require the subagent contract.
  4. ``test_cli_help_advertises_subagent_options`` --
     ``ralph smoke-headless-claude --help`` advertises both
     ``--subagents`` and ``--subagent-prompt-file`` (the CLI
     surface contract).
  5. ``test_cli_help_for_both_claude_commands_advertise_same_subagent_options`` --
     ``smoke-interactive-claude`` and ``smoke-headless-claude`` show
     the same two subagent options so operators see one
     consistent surface.

The tests use deterministic fixture tests (no real subprocess,
no real file I/O outside the in-memory ``CliRunner`` runner,
no real wall-clock waits). The harness plumbing is monkeypatched
so the test does not depend on a real ``claude-headless`` binary
on ``PATH`` and does not consume tokens.

The tests carry the ``@pytest.mark.smoke`` marker. The maintained
verification mark expression
(``ralph-workflow/ralph/test_suites.py:107``) is
``(not subprocess_e2e and not smoke) or required_auto_integrate_e2e``,
so ``smoke``-marked tests are excluded from the default
``make test`` selection. The two live ``ralph smoke-*-claude``
invocations themselves are the manual token-burning runs (per R8)
that stay out of ``make verify``.

Test isolation guarantees (per ``docs/agents/testing-guide.md``):

  - No real subprocess (the harness plumbing is monkeypatched).
  - No real filesystem outside ``CliRunner``'s in-memory
    virtual FS.
  - No real wall-clock waits.
  - No module-level mutable accumulators.
  - No ``noqa`` directives (audit_lint_bypass).
  - No bare ``type: ignore`` comments (audit_typecheck_bypass).
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from ralph.cli.commands import smoke as smoke_module
from ralph.cli.main import app

# Smoke tests are excluded by default (``addopts = -m "not smoke"``
# in ``pytest.ini``). The maintained verification mark expression
# in ``ralph/test_suites.py:107`` is
# ``(not subprocess_e2e and not smoke) or required_auto_integrate_e2e``,
# so ``smoke``-marked tests are excluded from the default
# ``make test`` selection. The two live ``ralph smoke-*-claude``
# invocations themselves are the manual token-burning runs
# (per R8) that stay out of ``make verify``; this file's
# tests are deterministic harness-fixture tests that pin the
# command's CLI surface so a future regression in the
# registration cannot silently remove the headless smoke
# command from ``ralph --help``.
pytestmark = pytest.mark.smoke


_RUNNER = CliRunner()


def test_smoke_headless_claude_command_default_agent_alias() -> None:
    """AC #1: the default ``agent_name`` for the headless smoke command is ``claude-headless/haiku``.

    The headless transport's default alias lives at
    ``smoke_module._HEADLESS_CLAUDE_AGENT``; the value is the
    canonical ``claude-headless/haiku`` string so the harness
    looks up the same ``claude-headless`` ``AgentConfig`` the
    production pipeline uses. The interactive smoke's
    equivalent constant is ``_INTERACTIVE_AGENT = "claude/haiku"``
    (verified separately in ``tests/test_cli_smoke.py``); this
    test pins the headless counterpart to keep the two
    transports in lock-step.
    """
    assert smoke_module._HEADLESS_CLAUDE_AGENT == "claude-headless/haiku"


def test_smoke_headless_claude_command_delegates_to_shared_harness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC #2: ``smoke_headless_claude_command`` is a thin pass-through.

    The command forwards ``display_context``, ``pro_hooks``,
    ``model_identity``, ``subagents``, and ``subagent_prompt_file``
    to ``smoke_harness_agent_command`` with the headless alias
    as the first positional argument. Operators can rely on the
    same plumbing for both Claude transports; the harness's
    per-check grading does not duplicate between commands.
    """
    captured: dict[str, object] = {}

    def fake_harness(
        agent_name: str,
        *,
        display_context: object,
        pro_hooks: object,
        model_identity: object,
        subagents: bool,
        subagent_prompt_file: object,
    ) -> int:
        captured["agent_name"] = agent_name
        captured["display_context"] = display_context
        captured["pro_hooks"] = pro_hooks
        captured["model_identity"] = model_identity
        captured["subagents"] = subagents
        captured["subagent_prompt_file"] = subagent_prompt_file
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
    )

    assert exit_code == 0
    assert captured["agent_name"] == "claude-headless/haiku"
    assert captured["display_context"] is sentinel_context
    assert captured["pro_hooks"] is sentinel_hooks
    assert captured["model_identity"] is sentinel_identity
    assert captured["subagents"] is True
    assert captured["subagent_prompt_file"] is sentinel_prompt


def test_smoke_headless_claude_command_default_no_subagents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC #3: the default ``subagents`` flag is ``False``.

    A no-subagent invocation does not require the subagent
    contract: the harness grades the basic scenario (artifact
    submission, completion sentinel, tool activity) and
    ``subagents`` is False. This is the same default the
    interactive smoke uses, so operators can run either
    command without flags and get the same behavior.
    """
    captured: dict[str, object] = {}

    def fake_harness(
        agent_name: str,
        *,
        display_context: object,
        pro_hooks: object,
        model_identity: object,
        subagents: bool,
        subagent_prompt_file: object,
    ) -> int:
        captured["subagents"] = subagents
        captured["subagent_prompt_file"] = subagent_prompt_file
        return 0

    monkeypatch.setattr(smoke_module, "smoke_harness_agent_command", fake_harness)

    exit_code = smoke_module.smoke_headless_claude_command(display_context=None)

    assert exit_code == 0
    assert captured["subagents"] is False
    assert captured["subagent_prompt_file"] is None


def test_cli_help_advertises_subagent_options() -> None:
    """AC #4: ``ralph smoke-headless-claude --help`` advertises both subagent options.

    The CLI surface contract requires both ``--subagents`` and
    ``--subagent-prompt-file`` to appear in the help text. The
    Typer ``CliRunner`` exercises the same registration path
    operators hit at the terminal; the help text must be
    authoritative so a future operator does not silently lose
    the subagent contract.
    """
    result = _RUNNER.invoke(app, ["smoke-headless-claude", "--help"])
    assert result.exit_code == 0
    output = result.stdout
    assert "--subagents" in output
    assert "--subagent-prompt-file" in output


def test_cli_help_for_both_claude_commands_advertise_same_subagent_options() -> None:
    """AC #5: both ``smoke-interactive-claude`` and ``smoke-headless-claude`` show the same subagent options.

    Operators see one consistent CLI surface for the two
    Claude transports. A regression that drops
    ``--subagent-prompt-file`` from the headless command (or
    changes its spelling) is surfaced here.
    """
    interactive_help = _RUNNER.invoke(app, ["smoke-interactive-claude", "--help"])
    headless_help = _RUNNER.invoke(app, ["smoke-headless-claude", "--help"])
    assert interactive_help.exit_code == 0
    assert headless_help.exit_code == 0

    for option in ("--subagents", "--subagent-prompt-file"):
        assert option in interactive_help.stdout, (
            f"smoke-interactive-claude --help is missing {option}"
        )
        assert option in headless_help.stdout, (
            f"smoke-headless-claude --help is missing {option}"
        )
