"""Unit tests for CLI."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.cli.main import (
    app,
)
from ralph.display.context import DisplayContext, make_display_context

if TYPE_CHECKING:
    from rich.console import Console
    from typer.testing import CliRunner

RUN_PIPELINE_SUCCESS = 42
KEYBOARD_INTERRUPT_EXIT_CODE = 130
USAGE_ERROR_EXIT_CODE = 2
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_POLICY_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


def _make_display_context_for_console(console: Console) -> DisplayContext:
    """Create a DisplayContext for a given console."""
    return make_display_context(console=console, env={})


class TestRemovedReviewFlags:
    """Verify that review-era CLI flags that no longer exist are absent from help output."""

    @pytest.mark.parametrize(
        "flag",
        [
            "--reviewer-reviews",
            "--reviewer-agent",
            "--reviewer-model",
            "--review-depth",
        ],
    )
    def test_removed_review_flags_not_in_help(self, cli_runner: CliRunner, flag: str) -> None:
        result = cli_runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert flag not in result.stdout

    def test_quick_flag_is_in_help(self, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "--quick" in result.stdout or "-Q" in result.stdout

    def test_new_shortcuts_are_in_help(self, cli_runner: CliRunner) -> None:
        result = cli_runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "--thorough" in result.stdout or "-T" in result.stdout
        assert "--resume" in result.stdout or "-r" in result.stdout
        assert "--check-config" in result.stdout or "-C" in result.stdout
        assert "--dry-run" in result.stdout
        dry_run_lines = [line for line in result.stdout.splitlines() if "--dry-run" in line]
        assert dry_run_lines
        assert all("-n" not in line for line in dry_run_lines)
