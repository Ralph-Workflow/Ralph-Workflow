from __future__ import annotations

import pytest
from typer.testing import CliRunner

from ralph.cli.main import app


def _noop_bootstrap(
    *,
    display_context: object = None,
    emit_welcome: bool = True,
) -> None:
    del display_context, emit_welcome


@pytest.fixture(autouse=True)
def _wide_help_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr("ralph.cli.main.bootstrap_global_configs", _noop_bootstrap)


_SMOKE_COMMANDS_WITH_MULTIMODAL = (
    "smoke-interactive-claude",
    "smoke-headless-claude",
    "smoke-interactive-agy",
    "smoke-interactive-nanocoder",
    "smoke-interactive-cursor",
    "smoke-interactive-opencode",
)


def _invoke_help(cmd_name: str) -> tuple[int, str]:
    runner = CliRunner()
    result = runner.invoke(app, [cmd_name, "--help"], prog_name="ralph")
    output = result.output
    if result.exit_code != 0 and result.exception:
        import traceback

        output = (
            f"{result.output}\nException: {''.join(traceback.format_exception(result.exception))}"
        )
    return result.exit_code, output


@pytest.mark.timeout_seconds(5)
def test_smoke_interactive_claude_help_advertises_multimodal() -> None:
    exit_code, output = _invoke_help("smoke-interactive-claude")
    assert exit_code == 0, output
    assert "--multimodal" in output


def test_smoke_headless_claude_help_advertises_multimodal() -> None:
    exit_code, output = _invoke_help("smoke-headless-claude")
    assert exit_code == 0, output
    assert "--multimodal" in output


def test_smoke_interactive_agy_help_advertises_multimodal() -> None:
    exit_code, output = _invoke_help("smoke-interactive-agy")
    assert exit_code == 0, output
    assert "--multimodal" in output


def test_smoke_interactive_nanocoder_help_advertises_multimodal() -> None:
    exit_code, output = _invoke_help("smoke-interactive-nanocoder")
    assert exit_code == 0, output
    assert "--multimodal" in output


def test_smoke_interactive_cursor_help_advertises_multimodal() -> None:
    exit_code, output = _invoke_help("smoke-interactive-cursor")
    assert exit_code == 0, output
    assert "--multimodal" in output


def test_smoke_interactive_opencode_help_advertises_multimodal() -> None:
    exit_code, output = _invoke_help("smoke-interactive-opencode")
    assert exit_code == 0, output
    assert "--multimodal" in output
