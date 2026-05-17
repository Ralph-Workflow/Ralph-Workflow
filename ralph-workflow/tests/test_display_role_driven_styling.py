"""Regression tests: display layer uses role-derived styling, not canonical phase names.

Verifies that completion_summary and phase_banner derive styles from phase roles
when pipeline_policy is provided, rather than from hardcoded canonical phase names.
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
from typing import Any

from rich.console import Console

from ralph.display.completion_summary import (
    CompletionSummaryOptions,
    render_completion_summary_group,
)
from ralph.display.context import make_display_context
from ralph.display.phase_banner import phase_style
from ralph.display.snapshot import PipelineSnapshot
from ralph.display.theme import RALPH_THEME
from ralph.policy.models import (
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    RecoveryPolicy,
)


def _make_custom_pipeline_policy() -> PipelinePolicy:
    """Custom PipelinePolicy: design (execution), sign_off (commit), done/failed (terminal)."""
    return PipelinePolicy(
        entry_phase="design",
        terminal_phase="done",
        phases={
            "design": PhaseDefinition(
                drain="design",
                role="execution",
                transitions=PhaseTransition(on_success="sign_off"),
            ),
            "sign_off": PhaseDefinition(
                drain="sign_off",
                role="commit",
                transitions=PhaseTransition(on_success="done", on_loopback="sign_off"),
            ),
            "failed_terminal": PhaseDefinition(
                drain="failed_terminal",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(
                    on_success="failed_terminal", on_loopback="failed_terminal"
                ),
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done", on_loopback="done"),
            ),
        },
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )


def _minimal_snapshot(**kwargs: object) -> PipelineSnapshot:
    defaults: dict[str, Any] = {
        "phase": "done",
        "previous_phase": None,
        "review_issues_found": False,
        "interrupted_by_user": False,
        "last_error": None,
        "pr_url": None,
        "push_count": 0,
        "total_agent_calls": 1,
        "total_continuations": 0,
        "total_fallbacks": 0,
        "total_retries": 0,
        "workers": (),
        "prompt_path": None,
        "prompt_preview": (),
        "run_id": "test-run",
        "created_at": datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
        "decision_log": (),
        "is_terminal_failure": False,
        "is_terminal_success": True,
    }
    defaults.update(kwargs)
    return PipelineSnapshot(**defaults)


def _console() -> Console:
    buf = StringIO()
    return Console(
        file=buf,
        color_system="truecolor",
        force_terminal=True,
        no_color=False,
        theme=RALPH_THEME,
        width=200,
        highlight=False,
    )


class TestPhaseStyleIsRoleDriven:
    """_phase_style returns role-derived style when pipeline_policy is provided."""

    def test_custom_execution_phase_gets_execution_style(self) -> None:
        policy = _make_custom_pipeline_policy()
        style = phase_style("design", policy)
        # execution role maps to "theme.phase.development"
        assert style == "theme.phase.development"

    def test_custom_commit_phase_gets_commit_style(self) -> None:
        policy = _make_custom_pipeline_policy()
        style = phase_style("sign_off", policy)
        assert style == "theme.phase.commit"

    def test_custom_terminal_failure_phase_gets_failed_style(self) -> None:
        policy = _make_custom_pipeline_policy()
        style = phase_style("failed_terminal", policy)
        assert style == "theme.phase.failed"

    def test_custom_terminal_success_phase_gets_complete_style(self) -> None:
        policy = _make_custom_pipeline_policy()
        style = phase_style("done", policy)
        assert style == "theme.phase.complete"

    def test_without_policy_canonical_name_returns_muted(self) -> None:
        # Without policy, canonical phase names are not recognized (only roles are)
        style = phase_style("planning")
        assert style == "theme.text.muted"

    def test_unknown_phase_without_policy_returns_muted(self) -> None:
        style = phase_style("some_custom_phase")
        assert style == "theme.text.muted"


class TestCompletionSummaryRoleDrivenStyling:
    """render_completion_summary_group uses role-derived section styles when policy provided."""

    def test_renders_without_error_with_custom_policy(self) -> None:
        """render_completion_summary_group accepts pipeline_policy and renders without error."""
        policy = _make_custom_pipeline_policy()
        snapshot = _minimal_snapshot(plan_summary="Test plan", last_error="Test error")
        console = _console()
        ctx = make_display_context(console=console)

        group = render_completion_summary_group(
            snapshot,
            display_context=ctx,
            options=CompletionSummaryOptions(
                pipeline_policy=policy,
                include_context_sections=True,
            ),
        )
        # Just verifying it renders without exception
        console.print(group, markup=False, highlight=False)
        file_obj = console.file
        assert isinstance(file_obj, StringIO)
        output = file_obj.getvalue()
        assert "Pipeline" in output

    def test_renders_without_error_without_policy(self) -> None:
        """render_completion_summary_group still works when pipeline_policy is None."""
        snapshot = _minimal_snapshot()
        console = _console()
        ctx = make_display_context(console=console)

        group = render_completion_summary_group(snapshot, display_context=ctx)
        console.print(group, markup=False, highlight=False)
        file_obj = console.file
        assert isinstance(file_obj, StringIO)
        output = file_obj.getvalue()
        assert "Pipeline" in output
