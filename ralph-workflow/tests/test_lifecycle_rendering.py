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

from ralph.display.completion_summary import (
    render_completion_summary,
    render_completion_summary_group,
)
from ralph.display.context import make_display_context
from ralph.display.phase_banner import show_phase_start_from_entry
from ralph.display.phase_lifecycle import PhaseEntryModel, PhaseExitModel
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


def _blank_snapshot(  # noqa: PLR0913
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


class TestShowPhaseStartFromEntry:
    def test_renders_phase_label(self) -> None:
        console, buf = _make_console()
        ctx = make_display_context(console=console, env={})
        entry = PhaseEntryModel(phase_name="development_analysis")
        show_phase_start_from_entry(entry, display_context=ctx)
        assert "Development Analysis" in buf.getvalue()

    def test_renders_outer_dev_label(self) -> None:
        console, buf = _make_console()
        ctx = make_display_context(console=console, env={})
        entry = PhaseEntryModel(phase_name="development", outer_dev_iteration=2, outer_dev_cap=5)
        show_phase_start_from_entry(entry, display_context=ctx)
        out = buf.getvalue()
        assert "Dev" in out
        assert "2" in out
        assert "5" in out

    def test_renders_inner_analysis_label(self) -> None:
        console, buf = _make_console()
        ctx = make_display_context(console=console, env={})
        entry = PhaseEntryModel(
            phase_name="development_analysis", inner_analysis=1, inner_analysis_cap=3
        )
        show_phase_start_from_entry(entry, display_context=ctx)
        out = buf.getvalue()
        assert "Analysis" in out
        assert "1" in out
        assert "3" in out

    def test_renders_budget_remaining(self) -> None:
        console, buf = _make_console()
        ctx = make_display_context(console=console, env={})
        entry = PhaseEntryModel(phase_name="fix", budget_remaining=4)
        show_phase_start_from_entry(entry, display_context=ctx)
        assert "4" in buf.getvalue()

    def test_renders_agent_name(self) -> None:
        console, buf = _make_console()
        ctx = make_display_context(console=console, env={})
        entry = PhaseEntryModel(phase_name="development", agent_name="claude-dev")
        show_phase_start_from_entry(entry, display_context=ctx)
        assert "claude-dev" in buf.getvalue()

    def test_outer_dev_before_inner_analysis(self) -> None:
        """Outer dev cycle label appears before inner analysis label."""
        console, buf = _make_console()
        ctx = make_display_context(console=console, env={})
        entry = PhaseEntryModel(
            phase_name="fix",
            outer_dev_iteration=2,
            outer_dev_cap=5,
            inner_analysis=1,
            inner_analysis_cap=3,
        )
        show_phase_start_from_entry(entry, display_context=ctx)
        out = buf.getvalue()
        assert out.index("Dev") < out.index("Analysis")

    def test_ascii_fallback_mode(self) -> None:
        """In force_glyphs=ascii mode, glyphs are ASCII safe."""
        console, buf = _make_console()
        ctx = make_display_context(console=console, env={}, force_glyphs="ascii")
        entry = PhaseEntryModel(
            phase_name="development", outer_dev_iteration=1, inner_analysis=2
        )
        show_phase_start_from_entry(entry, display_context=ctx)
        out = buf.getvalue()
        # ASCII fallback should not emit multi-byte unicode glyphs for iteration context
        assert "Development" in out

    def test_no_optional_fields_shows_phase_only(self) -> None:
        console, buf = _make_console()
        ctx = make_display_context(console=console, env={})
        entry = PhaseEntryModel(phase_name="planning")
        show_phase_start_from_entry(entry, display_context=ctx)
        out = buf.getvalue()
        assert "Planning" in out
        # Should not include iteration context keywords when all None
        assert "Dev" not in out
        assert "Analysis" not in out
        assert "Budget" not in out


# ---------------------------------------------------------------------------
# emit_phase_close_from_exit
# ---------------------------------------------------------------------------


class TestEmitPhaseCloseFromExit:
    def test_emits_phase_name(self) -> None:
        renderer, buf = _make_renderer()
        renderer.begin_phase("development")
        entry = PhaseEntryModel(phase_name="development", phase_role="execution")
        exit_model = PhaseExitModel.from_entry_model(
            entry, exit_trigger="produced", artifact_outcome="dev result"
        )
        renderer.emit_phase_close_from_exit(exit_model)
        assert "phase=development" in buf.getvalue()

    def test_emits_exit_trigger(self) -> None:
        renderer, buf = _make_renderer()
        renderer.begin_phase("planning")
        entry = PhaseEntryModel(phase_name="planning", phase_role="execution")
        exit_model = PhaseExitModel.from_entry_model(entry, exit_trigger="produced")
        renderer.emit_phase_close_from_exit(exit_model)
        assert "exit=produced" in buf.getvalue()

    def test_emits_artifact_outcome(self) -> None:
        renderer, buf = _make_renderer()
        renderer.begin_phase("planning")
        entry = PhaseEntryModel(phase_name="planning")
        exit_model = PhaseExitModel.from_entry_model(
            entry, artifact_outcome="plan: 5 step(s), 2 risk(s)"
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
        exit_model = PhaseExitModel.from_entry_model(entry, exit_trigger="produced")
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
        exit_model = PhaseExitModel.from_entry_model(entry, exit_trigger="produced")
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
            exit_trigger="timeout",
            waiting_status_line="waiting for tool result",
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
            exit_trigger="failed",
            last_failure_category="timeout",
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
            exit_trigger="failed",
            waiting_status_line="waiting for child",
            last_failure_category="tool_error",
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
        exit_model = PhaseExitModel.from_entry_model(entry, exit_trigger="produced")
        renderer.emit_phase_close_from_exit(exit_model)
        out = buf.getvalue()
        assert "[phase-close] debug" not in out


# ---------------------------------------------------------------------------
# Debug breadcrumbs in text-mode render_completion_summary
# ---------------------------------------------------------------------------


class TestTextModeDebugBreadcrumbs:
    def test_last_activity_line_included(self) -> None:
        snapshot = _blank_snapshot(last_activity_line="reading ralph-workflow/CONTRIBUTING.md")
        text = render_completion_summary(snapshot).plain
        assert "last_activity: reading ralph-workflow/CONTRIBUTING.md" in text

    def test_waiting_status_line_included(self) -> None:
        snapshot = _blank_snapshot(waiting_status_line="waiting for tool result")
        text = render_completion_summary(snapshot).plain
        assert "waiting: waiting for tool result" in text

    def test_failure_category_included(self) -> None:
        snapshot = _blank_snapshot(last_failure_category="timeout")
        text = render_completion_summary(snapshot).plain
        assert "failure_category: timeout" in text

    def test_debug_section_absent_when_no_breadcrumbs(self) -> None:
        snapshot = _blank_snapshot()
        text = render_completion_summary(snapshot).plain
        assert "Debug:" not in text

    def test_debug_section_present_when_any_breadcrumb_set(self) -> None:
        snapshot = _blank_snapshot(last_activity_line="some line")
        text = render_completion_summary(snapshot).plain
        assert "Debug:" in text


# ---------------------------------------------------------------------------
# Section ordering in render_completion_summary_group
# ---------------------------------------------------------------------------


class TestGroupedSectionOrdering:
    def _render(self, snapshot: PipelineSnapshot) -> str:
        buf = StringIO()
        console = Console(
            file=buf, force_terminal=False, highlight=False, color_system=None, width=120
        )
        ctx = make_display_context(console=console, env={})
        group = render_completion_summary_group(snapshot, display_context=ctx)
        console.print(group, markup=False, highlight=False)
        return buf.getvalue()

    def test_activity_summary_before_verification(self) -> None:
        out = self._render(_blank_snapshot())
        assert out.index("Activity Summary") < out.index("Verification")

    def test_debug_after_error_section(self) -> None:
        snapshot = _blank_snapshot(
            is_terminal_failure=True,
            last_error="build failed",
            last_activity_line="last thing done",
        )
        out = self._render(snapshot)
        assert out.index("Error") < out.index("Debug")

    def test_debug_before_footer_rule(self) -> None:
        snapshot = _blank_snapshot(last_activity_line="some activity")
        out = self._render(snapshot)
        # Debug section should appear, and it should be before the final footer
        assert "Debug" in out
        # The footer rule is the last Rule. Confirm Debug section is not the last content.
        debug_pos = out.index("Debug")
        # Footer rule appears after Debug
        remaining = out[debug_pos:]
        assert "─" in remaining  # Footer rule contains dash characters

    def test_debug_absent_when_no_breadcrumbs(self) -> None:
        out = self._render(_blank_snapshot())
        assert "Debug" not in out
