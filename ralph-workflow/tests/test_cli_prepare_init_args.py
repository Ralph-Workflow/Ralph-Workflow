"""Unit tests for CLI."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.cli.main import (
    prepare_init_args,
)
from ralph.display.context import DisplayContext, make_display_context

if TYPE_CHECKING:
    import pytest
    from rich.console import Console

RUN_PIPELINE_SUCCESS = 42
KEYBOARD_INTERRUPT_EXIT_CODE = 130
USAGE_ERROR_EXIT_CODE = 2
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_POLICY_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


def _make_display_context_for_console(console: Console) -> DisplayContext:
    """Create a DisplayContext for a given console."""
    return make_display_context(console=console, env={})


class TestPrepareInitArgs:
    """Tests for _prepare_init_args sys.argv fallback."""

    def test_none_falls_back_to_sys_argv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.argv", ["ralph", "-Q", "do a quick change", "--dry-run"])
        result = prepare_init_args(None)
        assert result == ["-Q", "--prompt", "do a quick change", "--dry-run"]

    def test_explicit_args_bypass_sys_argv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.argv", ["ralph", "--should-not-be-used"])
        result = prepare_init_args(["-Q", "task"])
        assert result == ["-Q", "--prompt", "task"]
