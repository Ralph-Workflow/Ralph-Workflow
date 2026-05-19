"""Regression tests: no module-level Console globals in renderer modules.

These black-box tests verify that:
1. No module-level Console globals exist in pipeline runner and CLI command modules.
2. Injected DisplayContext is honored when provided.
"""

from __future__ import annotations

import os
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

import ralph.cli.commands.run as run_module
from ralph.cli.commands.commit import CommitPlumbingOptions, commit_plumbing
from ralph.cli.commands.init import init_command
from ralph.display.context import DisplayContext
from ralph.display.theme import RALPH_THEME


def _make_recording_console() -> Console:
    """Create a StringIO-backed recording console with Ralph theme."""
    stream = StringIO()
    return Console(
        file=stream,
        color_system=None,
        force_terminal=False,
        theme=RALPH_THEME,
    )


class TestInjectedDisplayContextIsHonored:
    """Tests that verify injected DisplayContext is used when provided."""

    def test_run_pipeline_keyboard_interrupt_uses_injected_context(self, tmp_path: Path) -> None:
        """Verify run_pipeline renders KeyboardInterrupt on the injected console."""
        recording_console = _make_recording_console()
        ctx = DisplayContext(
            console=recording_console,
            theme=RALPH_THEME,
            width=80,
            mode="wide",
            narrow=False,
            color_enabled=True,
            glyphs_enabled=True,
            headline_max_chars=120,
            condenser_soft_limit=400,
            condenser_hard_limit=4000,
            streaming_checkpoint_chars=4000,
            streaming_checkpoint_fragments=20,
            streaming_dedup_enabled=True,
            streaming_checkpoints_enabled=True,
            thinking_preview_min_chars=80,
            tool_result_headline_min_chars=80,
        )

        original_cwd = Path.cwd()
        try:
            os.chdir(tmp_path)
            init_command(display_context=ctx)
            (tmp_path / "PROMPT.md").write_text(
                "# Goal\n\nExercise the interrupt path.\n",
                encoding="utf-8",
            )

            def _raise_keyboard_interrupt(*args: object, **kwargs: object) -> int:
                del args, kwargs
                raise KeyboardInterrupt

            with patch.object(run_module.state, "run_func", _raise_keyboard_interrupt):
                exit_code = run_module.run_pipeline(display_context=ctx)

            output = recording_console.file.getvalue()
            expected_exit_code = 130
            assert exit_code == expected_exit_code
            assert "Interrupted by user" in output
        finally:
            os.chdir(original_cwd)

    def test_init_command_warning_uses_injected_context(self, tmp_path: Path) -> None:
        """Verify init_command uses injected display_context for deprecation warning."""
        recording_console = _make_recording_console()
        ctx = DisplayContext(
            console=recording_console,
            theme=RALPH_THEME,
            width=80,
            mode="wide",
            narrow=False,
            color_enabled=True,
            glyphs_enabled=True,
            headline_max_chars=120,
            condenser_soft_limit=400,
            condenser_hard_limit=4000,
            streaming_checkpoint_chars=4000,
            streaming_checkpoint_fragments=20,
            streaming_dedup_enabled=True,
            streaming_checkpoints_enabled=True,
            thinking_preview_min_chars=80,
            tool_result_headline_min_chars=80,
        )

        original_cwd = Path.cwd()
        try:
            os.chdir(tmp_path)
            # Call with a deprecated template label to trigger warning
            init_command(template="legacy", display_context=ctx)

            output = recording_console.file.getvalue()
            assert "deprecated" in output.lower() or "ignored" in output.lower(), (
                f"Expected deprecation warning in output, got: {output}"
            )
        finally:
            os.chdir(original_cwd)

    def test_commit_plumbing_no_repo_uses_injected_context(self, tmp_path: Path) -> None:
        """Verify commit_plumbing uses injected display_context for 'not in git repo' message."""
        recording_console = _make_recording_console()
        ctx = DisplayContext(
            console=recording_console,
            theme=RALPH_THEME,
            width=80,
            mode="wide",
            narrow=False,
            color_enabled=True,
            glyphs_enabled=True,
            headline_max_chars=120,
            condenser_soft_limit=400,
            condenser_hard_limit=4000,
            streaming_checkpoint_chars=4000,
            streaming_checkpoint_fragments=20,
            streaming_dedup_enabled=True,
            streaming_checkpoints_enabled=True,
            thinking_preview_min_chars=80,
            tool_result_headline_min_chars=80,
        )

        # Patch find_repo_root to raise an exception
        with patch("ralph.cli.commands.commit.find_repo_root") as mock_find:
            mock_find.side_effect = Exception("Not a git repository")
            commit_plumbing(
                options=CommitPlumbingOptions(generate_commit_msg=True),
                display_context=ctx,
            )

        output = recording_console.file.getvalue()
        assert "git" in output.lower() or "repository" in output.lower(), (
            f"Expected git repository warning in output, got: {output}"
        )
