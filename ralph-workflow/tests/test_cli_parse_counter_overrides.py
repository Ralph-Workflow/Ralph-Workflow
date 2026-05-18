"""Unit tests for CLI."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import rich_click as click
from typer.testing import CliRunner as TyperCliRunner

from ralph.cli.main import (
    app,
    parse_counter_overrides,
)
from ralph.display.context import DisplayContext, make_display_context

if TYPE_CHECKING:
    from rich.console import Console

RUN_PIPELINE_SUCCESS = 42
KEYBOARD_INTERRUPT_EXIT_CODE = 130
USAGE_ERROR_EXIT_CODE = 2
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_POLICY_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


def _make_display_context_for_console(console: Console) -> DisplayContext:
    """Create a DisplayContext for a given console."""
    return make_display_context(console=console, env={})


class CliResult:
    def __init__(self, exit_code: int, stdout: str, stderr: str) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class CliRunner:
    def __init__(self) -> None:
        self._cwd = PROJECT_ROOT
        self._runner = TyperCliRunner()

    def invoke(self, _app: object, args: list[str]) -> CliResult:
        with self._pushd(self._cwd):
            result = self._runner.invoke(app, args, catch_exceptions=False)
        stderr = getattr(result, "stderr", "")
        return CliResult(result.exit_code, result.stdout, stderr)

    @contextmanager
    def _pushd(self, path: Path) -> object:
        original_cwd = Path.cwd()
        try:
            os.chdir(path)
            yield
        finally:
            os.chdir(original_cwd)

    @contextmanager
    def isolated_filesystem(self, temp_dir: Path) -> object:
        temp_dir.mkdir(parents=True, exist_ok=True)
        with self._runner.isolated_filesystem(temp_dir):
            yield temp_dir


class TestParseCounterOverrides:
    """Tests for _parse_counter_overrides helper."""

    def test_parses_single_valid_entry(self) -> None:
        result = parse_counter_overrides(["iteration=3"])
        assert result == {"iteration": 3}

    def test_parses_multiple_entries(self) -> None:
        result = parse_counter_overrides(["iteration=3", "reviewer_pass=1"])
        assert result == {"iteration": 3, "reviewer_pass": 1}

    def test_empty_list_returns_empty_dict(self) -> None:
        assert parse_counter_overrides([]) == {}

    def test_missing_equals_raises_usage_error(self) -> None:
        with pytest.raises(click.UsageError, match="invalid format"):
            parse_counter_overrides(["iteration3"])

    def test_blank_name_raises_usage_error(self) -> None:
        with pytest.raises(click.UsageError, match="blank counter name"):
            parse_counter_overrides(["=5"])

    def test_non_integer_value_raises_usage_error(self) -> None:
        with pytest.raises(click.UsageError, match="not a valid integer"):
            parse_counter_overrides(["iteration=abc"])

    def test_zero_value_is_valid(self) -> None:
        result = parse_counter_overrides(["reviewer_pass=0"])
        assert result == {"reviewer_pass": 0}

