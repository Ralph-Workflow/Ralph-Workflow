"""Black-box tests for lifecycle model-driven rendering APIs.

Covers:
- show_phase_start_from_entry (phase_banner.py)
- emit_phase_close_from_exit (PlainLogRenderer / ParallelDisplay)
- debug breadcrumbs in text-mode render_completion_summary
- section ordering in render_completion_summary_group
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.phase_lifecycle import ExitContext, PhaseEntryModel, PhaseExitModel
from ralph.display.plain_renderer import PlainLogRenderer
from ralph.display.snapshot import PipelineSnapshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CREATED_AT = datetime(2026, 5, 4, tzinfo=UTC)


def _make_console(width: int = 200) -> tuple[Console, StringIO]:
    buf = StringIO()
    console = Console(
        file=buf, force_terminal=False, highlight=False, color_system=None, width=width
    )
    return console, buf


def _make_renderer() -> tuple[PlainLogRenderer, StringIO]:
    console, buf = _make_console()
    ctx = make_display_context(console=console, env={})
    return PlainLogRenderer(ctx), buf


def _blank_snapshot(
    *,
    phase: str = "terminal",
    is_terminal_success: bool = True,
    is_terminal_failure: bool = False,
    last_error: str | None = None,
    last_activity_line: str | None = None,
    waiting_status_line: str | None = None,
    last_failure_category: str | None = None,
) -> PipelineSnapshot:
    """Return a minimal PipelineSnapshot with all optional fields defaulted."""
    return PipelineSnapshot(
        phase=phase,
        previous_phase=None,
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=last_error,
        pr_url=None,
        push_count=0,
        total_agent_calls=0,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path=None,
        prompt_preview=(),
        run_id=None,
        created_at=_CREATED_AT,
        is_terminal_success=is_terminal_success,
        is_terminal_failure=is_terminal_failure,
        last_activity_line=last_activity_line,
        waiting_status_line=waiting_status_line,
        last_failure_category=last_failure_category,
    )


# ---------------------------------------------------------------------------
# show_phase_start_from_entry
# ---------------------------------------------------------------------------


class TestEmitPhaseCloseFromExit:
    def test_emits_phase_name(self) -> None:
        renderer, buf = _make_renderer()
        renderer.begin_phase("development")
        entry = PhaseEntryModel(phase_name="development", phase_role="execution")
        exit_model = PhaseExitModel.from_entry_model(
            entry, ExitContext(exit_trigger="produced", artifact_outcome="dev result")
        )
        renderer.emit_phase_close_from_exit(exit_model)
        assert "phase=development" in buf.getvalue()

    def test_emits_exit_trigger(self) -> None:
        renderer, buf = _make_renderer()
        renderer.begin_phase("planning")
        entry = PhaseEntryModel(phase_name="planning", phase_role="execution")
        exit_model = PhaseExitModel.from_entry_model(entry, ExitContext(exit_trigger="produced"))
        renderer.emit_phase_close_from_exit(exit_model)
        assert "exit=produced" in buf.getvalue()

    def test_emits_artifact_outcome(self) -> None:
        renderer, buf = _make_renderer()
        renderer.begin_phase("planning")
        entry = PhaseEntryModel(phase_name="planning")
        exit_model = PhaseExitModel.from_entry_model(
            entry, ExitContext(artifact_outcome="plan: 5 step(s), 2 risk(s)")
        )
        renderer.emit_phase_close_from_exit(exit_model)
        assert "plan: 5 step(s), 2 risk(s)" in buf.getvalue()

    def test_emits_iteration_labels_from_model(self) -> None:
        renderer, buf = _make_renderer()
        renderer.begin_phase("development")
        entry = PhaseEntryModel(
            phase_name="development",
            outer_dev_iteration=3,
            outer_dev_cap=5,
            inner_analysis=1,
            inner_analysis_cap=4,
        )
        exit_model = PhaseExitModel.from_entry_model(entry, ExitContext(exit_trigger="produced"))
        renderer.emit_phase_close_from_exit(exit_model)
        out = buf.getvalue()
        assert "Dev" in out
        assert "Analysis" in out

    def test_emits_elapsed_and_counters(self) -> None:
        renderer, buf = _make_renderer()
        renderer.begin_phase("development")
        # Emit some activity to populate counters
        renderer.emit_activity_line("u1", "tool_use", "read_file path.py")
        renderer.emit_activity_line("u1", "error", "some error")
        entry = PhaseEntryModel(phase_name="development")
        exit_model = PhaseExitModel.from_entry_model(entry, ExitContext(exit_trigger="produced"))
        renderer.emit_phase_close_from_exit(exit_model)
        out = buf.getvalue()
        assert "elapsed=" in out
        assert "tool_calls=1" in out
        assert "errors=1" in out

    def test_no_iteration_context_when_model_has_none(self) -> None:
        renderer, buf = _make_renderer()
        renderer.begin_phase("planning")
        entry = PhaseEntryModel(phase_name="planning")
        exit_model = PhaseExitModel.from_entry_model(entry)
        renderer.emit_phase_close_from_exit(exit_model)
        out = buf.getvalue()
        # Should not render [Dev] or [Analysis] brackets when no context
        assert "[Dev" not in out
        assert "[Analysis" not in out

    def test_emits_debug_line_when_waiting_status_set(self) -> None:
        renderer, buf = _make_renderer()
        renderer.begin_phase("development")
        entry = PhaseEntryModel(phase_name="development")
        exit_model = PhaseExitModel.from_entry_model(
            entry,
            ExitContext(exit_trigger="timeout", waiting_status_line="waiting for tool result"),
        )
        renderer.emit_phase_close_from_exit(exit_model)
        out = buf.getvalue()
        assert "[phase-close] debug phase=development" in out
        assert "waiting=waiting for tool result" in out

    def test_emits_debug_line_when_failure_category_set(self) -> None:
        renderer, buf = _make_renderer()
        renderer.begin_phase("development")
        entry = PhaseEntryModel(phase_name="development")
        exit_model = PhaseExitModel.from_entry_model(
            entry,
            ExitContext(exit_trigger="failed", last_failure_category="timeout"),
        )
        renderer.emit_phase_close_from_exit(exit_model)
        out = buf.getvalue()
        assert "[phase-close] debug phase=development" in out
        assert "failure_category=timeout" in out

    def test_emits_debug_line_when_both_breadcrumbs_set(self) -> None:
        renderer, buf = _make_renderer()
        renderer.begin_phase("fix")
        entry = PhaseEntryModel(phase_name="fix")
        exit_model = PhaseExitModel.from_entry_model(
            entry,
            ExitContext(
                exit_trigger="failed",
                waiting_status_line="waiting for child",
                last_failure_category="tool_error",
            ),
        )
        renderer.emit_phase_close_from_exit(exit_model)
        out = buf.getvalue()
        assert "[phase-close] debug phase=fix" in out
        assert "waiting=waiting for child" in out
        assert "failure_category=tool_error" in out

    def test_no_debug_line_when_no_breadcrumbs(self) -> None:
        renderer, buf = _make_renderer()
        renderer.begin_phase("development")
        entry = PhaseEntryModel(phase_name="development")
        exit_model = PhaseExitModel.from_entry_model(entry, ExitContext(exit_trigger="produced"))
        renderer.emit_phase_close_from_exit(exit_model)
        out = buf.getvalue()
        assert "[phase-close] debug" not in out

    def test_emits_review_issues_found(self) -> None:
        """emit_phase_close_from_exit emits review: issues found when review_issues_found=True."""
        renderer, buf = _make_renderer()
        renderer.begin_phase("review")
        entry = PhaseEntryModel(phase_name="review")
        exit_model = PhaseExitModel.from_entry_model(
            entry, ExitContext(exit_trigger="completed", review_issues_found=True)
        )
        renderer.emit_phase_close_from_exit(exit_model)
        out = buf.getvalue()
        assert "[phase-close] review: issues found" in out

    def test_emits_review_clean(self) -> None:
        """emit_phase_close_from_exit emits review: clean when review_issues_found=False."""
        renderer, buf = _make_renderer()
        renderer.begin_phase("review")
        entry = PhaseEntryModel(phase_name="review")
        exit_model = PhaseExitModel.from_entry_model(
            entry, ExitContext(exit_trigger="completed", review_issues_found=False)
        )
        renderer.emit_phase_close_from_exit(exit_model)
        out = buf.getvalue()
        assert "[phase-close] review: clean" in out

    def test_no_review_line_when_not_applicable(self) -> None:
        """emit_phase_close_from_exit emits no review line when review_issues_found is None."""
        renderer, buf = _make_renderer()
        renderer.begin_phase("development")
        entry = PhaseEntryModel(phase_name="development")
        exit_model = PhaseExitModel.from_entry_model(
            entry, ExitContext(exit_trigger="produced", review_issues_found=None)
        )
        renderer.emit_phase_close_from_exit(exit_model)
        out = buf.getvalue()
        assert "[phase-close] review:" not in out
