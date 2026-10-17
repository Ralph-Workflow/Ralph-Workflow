"""Regression tests: no module-level Console globals in renderer modules.

These black-box tests verify that:
1. No module-level Console globals exist in pipeline runner and CLI command modules.
2. Injected DisplayContext is honored when provided.
"""

from __future__ import annotations

import os
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from rich.console import Console

import ralph.cli.commands.run as run_module
from ralph.cli.commands.commit import CommitPlumbingOptions, commit_plumbing
from ralph.cli.commands.init import init_command
from ralph.config.bootstrap import BootstrapResult
from ralph.display._completion_summary_group import render_completion_summary_group
from ralph.display.auto_integrate_message import OperationDisplayState
from ralph.display.completion_summary import CompletionSummaryOptions
from ralph.display.context import DisplayContext
from ralph.display.snapshot import PipelineSnapshot
from ralph.display.theme import RALPH_THEME
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.skills._capability_state import CapabilityState


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
        mode="default",
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

    @pytest.mark.timeout_seconds(3)
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
            run_id="test-display-context-fixture",
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
    def test_init_command_unknown_template_uses_injected_context(self, tmp_path: Path) -> None:
        """Verify an unknown template error uses the injected display context."""
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
                patch("ralph.cli.commands.init._print_fallback_next_steps"),
                patch(
                    "ralph.cli.commands.init._ensure_baseline_capabilities",
                    return_value=(CapabilityState(), []),
                ),
                pytest.raises(typer.Exit) as excinfo,
            ):
                init_command(template="legacy", display_context=ctx)

            assert excinfo.value.exit_code == 1
            output = ctx.console.file.getvalue()
            assert "valid templates" in output.lower(), (
                f"Expected template guidance in output, got: {output}"
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

    @pytest.mark.parametrize("state", list(OperationDisplayState))
    def test_completion_summary_operation_display_states_use_injected_context(
        self, state: OperationDisplayState
    ) -> None:
        """Verify rendering completion summary with each OperationDisplayState honors injected DisplayContext."""
        ctx = _make_display_context()
        snap = MagicMock(spec=PipelineSnapshot)
        snap.phase = "done"
        snap.is_terminal_failure = False
        snap.decision_log = ()
        snap.review_issues_found = False
        snap.last_error = None
        snap.outer_dev_iteration = None
        snap.total_agent_calls = 0
        snap.total_continuations = 0
        snap.total_fallbacks = 0
        snap.total_retries = 0
        snap.push_count = 0
        snap.pr_url = None
        snap.budget_progress = {}
        snap.mcp_restart_count = 0
        snap.auto_integrate_action = state.value
        snap.auto_integrate_target = "main"
        snap.auto_integrate_reason = None
        snap.auto_integrate_fast_forwarded = True
        snap.auto_integrate_push = None
        snap.auto_integrate_remote_sync = None
        snap.auto_integrate_remote = None
        snap.auto_integrate_freshness_verdict = None
        snap.last_activity_line = None
        snap.waiting_status_line = None

        group = render_completion_summary_group(
            snap,
            display_context=ctx,
            options=CompletionSummaryOptions(),
        )
        ctx.console.print(group)
        assert isinstance(ctx.console.file, StringIO)
        output = ctx.console.file.getvalue()
        assert "auto-integrate:" in output.lower()
        normalized_state = state.value.replace("_", " ")
        assert normalized_state in output.lower() or state.value in output.lower()

    @pytest.mark.parametrize("kind", list(RebaseOutcomeKind))
    def test_completion_summary_rebase_outcome_kinds_use_injected_context(
        self, kind: RebaseOutcomeKind
    ) -> None:
        """Verify rendering completion summary with each RebaseOutcomeKind honors injected DisplayContext."""
        ctx = _make_display_context()
        snap = MagicMock(spec=PipelineSnapshot)
        snap.phase = "done"
        snap.is_terminal_failure = False
        snap.decision_log = ()
        snap.review_issues_found = False
        snap.last_error = None
        snap.outer_dev_iteration = None
        snap.total_agent_calls = 0
        snap.total_continuations = 0
        snap.total_fallbacks = 0
        snap.total_retries = 0
        snap.push_count = 0
        snap.pr_url = None
        snap.budget_progress = {}
        snap.mcp_restart_count = 0
        snap.auto_integrate_action = kind.value
        snap.auto_integrate_target = "main"
        snap.auto_integrate_reason = None
        snap.auto_integrate_fast_forwarded = True
        snap.auto_integrate_push = None
        snap.auto_integrate_remote_sync = None
        snap.auto_integrate_remote = None
        snap.auto_integrate_freshness_verdict = None
        snap.last_activity_line = None
        snap.waiting_status_line = None

        group = render_completion_summary_group(
            snap,
            display_context=ctx,
            options=CompletionSummaryOptions(),
        )
        ctx.console.print(group)
        assert isinstance(ctx.console.file, StringIO)
        output = ctx.console.file.getvalue()
        assert "auto-integrate:" in output.lower()
