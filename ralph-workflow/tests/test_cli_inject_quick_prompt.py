"""Unit tests for CLI."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.cli.main import (
    inject_quick_prompt,
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


class TestInjectQuickPrompt:
    """Tests for _inject_quick_prompt preprocessing helper."""

    def test_injects_prompt_flag_before_positional_text(self) -> None:
        result = inject_quick_prompt(["-Q", "do a quick change"])
        assert result == ["-Q", "--prompt", "do a quick change"]

    def test_long_quick_flag_also_triggers_injection(self) -> None:
        result = inject_quick_prompt(["--quick", "do a task"])
        assert result == ["--quick", "--prompt", "do a task"]

    def test_options_after_text_are_preserved(self) -> None:
        result = inject_quick_prompt(["-Q", "do a task", "--dry-run"])
        assert result == ["-Q", "--prompt", "do a task", "--dry-run"]

    def test_skips_injection_when_prompt_already_present(self) -> None:
        result = inject_quick_prompt(["-Q", "--prompt", "text"])
        assert result == ["-Q", "--prompt", "text"]

    def test_skips_injection_when_short_prompt_already_present(self) -> None:
        result = inject_quick_prompt(["-Q", "-P", "text"])
        assert result == ["-Q", "-P", "text"]

    def test_no_injection_when_no_quick_flag(self) -> None:
        result = inject_quick_prompt(["does-not-exist"])
        assert result == ["does-not-exist"]

    def test_known_subcommand_is_not_treated_as_prompt(self) -> None:
        result = inject_quick_prompt(["-Q", "cleanup"])
        assert result == ["-Q", "cleanup"]

    def test_no_positional_text_leaves_args_unchanged(self) -> None:
        result = inject_quick_prompt(["-Q", "--dry-run"])
        assert result == ["-Q", "--dry-run"]
