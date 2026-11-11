from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.cli.main import (
    app,
)
from ralph.display.context import DisplayContext, make_display_context

pytestmark = pytest.mark.timeout_seconds(5)

if TYPE_CHECKING:
    from rich.console import Console
    from typer.testing import CliRunner

RUN_PIPELINE_SUCCESS = 42
KEYBOARD_INTERRUPT_EXIT_CODE = 130
USAGE_ERROR_EXIT_CODE = 2
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_POLICY_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


def _make_display_context_for_console(console: Console) -> DisplayContext:
    return make_display_context(console=console, env={})


_REMOVED_REVIEW_FLAGS = (
    "--reviewer-reviews",
    "--reviewer-agent",
    "--reviewer-model",
    "--review-depth",
)


def _invoke_help(cli_runner: CliRunner) -> str:
    result = cli_runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    return result.stdout


class TestRemovedReviewFlags:
    def test_removed_review_flags_collectively_absent(self, cli_runner: CliRunner) -> None:
        stdout = _invoke_help(cli_runner)
        for flag in _REMOVED_REVIEW_FLAGS:
            assert flag not in stdout

    def test_quick_flag_is_in_help(self, cli_runner: CliRunner) -> None:
        stdout = _invoke_help(cli_runner)
        assert "--quick" in stdout or "-Q" in stdout

    def test_new_shortcuts_are_in_help(self, cli_runner: CliRunner) -> None:
        stdout = _invoke_help(cli_runner)
        assert "--thorough" in stdout or "-T" in stdout
        assert "--resume" in stdout or "-r" in stdout
        assert "--check-config" in stdout or "-C" in stdout
        assert "--dry-run" in stdout
        dry_run_lines = [line for line in stdout.splitlines() if "--dry-run" in line]
        assert dry_run_lines
        assert all("-n" not in line for line in dry_run_lines)
