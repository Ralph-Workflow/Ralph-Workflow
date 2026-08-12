"""Pin the ``--multimodal`` CLI surface across all six smoke commands (S-6).

The plan requires that ``--multimodal`` appear in the ``--help`` output of
every ``smoke-*`` command so the multimodal scenario is reachable on
every major coding harness (criterion 5). The test below enforces that
the option is in fact present on all six commands.

This file is NOT marked ``pytest.mark.smoke`` -- it inspects the CLI
``--help`` output via :class:`typer.testing.CliRunner`, not a real
subprocess, so it can run as part of the regular suite.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from ralph.cli.main import app

_RUNNER = CliRunner()


@pytest.fixture(autouse=True)
def _wide_help_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin a wide terminal for the duration of each parity test.

    rich_click's help renderer lays the option column out against the
    detected terminal width and drops long flags like ``--multimodal``
    when the width is narrow (xdist workers run without a TTY, so the
    detected width is whatever the environment happens to report).
    Setting ``COLUMNS`` per-test — instead of relying on the ambient
    shell width — keeps the assertion environment-independent.
    """
    monkeypatch.setenv("COLUMNS", "200")

#: Every smoke command that exposes ``--multimodal``. The plan locks
#: parity so the multimodal scenario is reachable on every major
#: coding harness (criterion 5).
_SMOKE_COMMANDS_WITH_MULTIMODAL = (
    "smoke-interactive-claude",
    "smoke-headless-claude",
    "smoke-interactive-agy",
    "smoke-interactive-nanocoder",
    "smoke-interactive-cursor",
    "smoke-interactive-opencode",
)


def _invoke_help(cmd_name: str) -> tuple[int, str]:
    result = _RUNNER.invoke(app, [cmd_name, "--help"], prog_name="ralph")
    return result.exit_code, result.output


def test_smoke_interactive_claude_help_advertises_multimodal() -> None:
    """The interactive Claude smoke command advertises ``--multimodal``."""
    exit_code, output = _invoke_help("smoke-interactive-claude")
    assert exit_code == 0, output
    assert "--multimodal" in output


def test_smoke_headless_claude_help_advertises_multimodal() -> None:
    """The headless Claude smoke command advertises ``--multimodal``."""
    exit_code, output = _invoke_help("smoke-headless-claude")
    assert exit_code == 0, output
    assert "--multimodal" in output


def test_smoke_interactive_agy_help_advertises_multimodal() -> None:
    """The AGY smoke command advertises ``--multimodal``."""
    exit_code, output = _invoke_help("smoke-interactive-agy")
    assert exit_code == 0, output
    assert "--multimodal" in output


def test_smoke_interactive_nanocoder_help_advertises_multimodal() -> None:
    """The Nanocoder smoke command advertises ``--multimodal``."""
    exit_code, output = _invoke_help("smoke-interactive-nanocoder")
    assert exit_code == 0, output
    assert "--multimodal" in output


def test_smoke_interactive_cursor_help_advertises_multimodal() -> None:
    """The Cursor smoke command advertises ``--multimodal``."""
    exit_code, output = _invoke_help("smoke-interactive-cursor")
    assert exit_code == 0, output
    assert "--multimodal" in output


def test_smoke_interactive_opencode_help_advertises_multimodal() -> None:
    """The OpenCode smoke command advertises ``--multimodal``."""
    exit_code, output = _invoke_help("smoke-interactive-opencode")
    assert exit_code == 0, output
    assert "--multimodal" in output

