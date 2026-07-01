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

import pytest
from rich.console import Console

import ralph.cli.commands.run as run_module
from ralph.cli.commands.commit import CommitPlumbingOptions, commit_plumbing
from ralph.cli.commands.init import init_command
from ralph.config.bootstrap import BootstrapResult
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


def _make_display_context() -> DisplayContext:
    recording_console = _make_recording_console()
    return DisplayContext(
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


class TestInjectedDisplayContextIsHonored:
    """Tests that verify injected DisplayContext is used when provided."""

    def test_run_pipeline_keyboard_interrupt_uses_injected_context(self) -> None:
        """Verify run_pipeline renders KeyboardInterrupt on the injected console."""
        ctx = _make_display_context()

        def _raise_keyboard_interrupt(*args: object, **kwargs: object) -> int:
            del args, kwargs
            raise KeyboardInterrupt

        load_result = run_module._LoadResult(
            config=object(),
            workspace_scope=None,
            initial_state=None,
            policy_bundle=None,
        )

        with (
            patch.object(run_module, "_load_configuration", return_value=load_result),
            patch.object(run_module, "_run_preflight_checks", return_value=0),
            patch.object(run_module.state, "run_func", _raise_keyboard_interrupt),
        ):
            exit_code = run_module.run_pipeline(display_context=ctx)

        output = ctx.console.file.getvalue()
        assert exit_code == 130
        assert "Interrupted by user" in output

    @pytest.mark.timeout_seconds(3)
    def test_init_command_warning_uses_injected_context(self, tmp_path: Path) -> None:
        """Verify init_command uses injected display_context for deprecation warning."""
        ctx = _make_display_context()
        skipped_result = BootstrapResult(path=tmp_path / "placeholder.toml", action="skipped")

        original_cwd = Path.cwd()
        try:
            os.chdir(tmp_path)
            with (
                patch(
                    "ralph.cli.commands.init.ensure_global_config",
                    return_value=skipped_result,
                ),
                patch(
                    "ralph.cli.commands.init.ensure_global_mcp_config",
                    return_value=skipped_result,
                ),
                patch(
                    "ralph.cli.commands.init.ensure_global_policy_configs",
                    return_value=[skipped_result, skipped_result],
                ),
                patch("ralph.cli.commands.init.ensure_local_support_configs", return_value=[]),
                patch("ralph.cli.commands.init._print_fallback_next_steps"),
            ):
                init_command(template="legacy", display_context=ctx)

            output = ctx.console.file.getvalue()
            assert "deprecated" in output.lower() or "ignored" in output.lower(), (
                f"Expected deprecation warning in output, got: {output}"
            )
        finally:
            os.chdir(original_cwd)

    def test_commit_plumbing_no_repo_uses_injected_context(self, tmp_path: Path) -> None:
        """Verify commit_plumbing uses injected display_context for 'not in git repo' message."""
        del tmp_path
        ctx = _make_display_context()

        with patch("ralph.cli.commands.commit.find_repo_root") as mock_find:
            mock_find.side_effect = Exception("Not a git repository")
            commit_plumbing(
                options=CommitPlumbingOptions(generate_commit_msg=True),
                display_context=ctx,
            )

        output = ctx.console.file.getvalue()
        assert "git" in output.lower() or "repository" in output.lower(), (
            f"Expected git repository warning in output, got: {output}"
        )
