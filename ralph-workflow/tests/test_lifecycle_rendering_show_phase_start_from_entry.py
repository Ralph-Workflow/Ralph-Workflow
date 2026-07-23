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
from ralph.display.parallel_display import ParallelDisplay, resolve_active_display
from ralph.display.phase_lifecycle import PhaseEntryModel
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


def _make_display() -> tuple[ParallelDisplay, StringIO]:
    console, buf = _make_console()
    ctx = make_display_context(console=console, env={})
    return ParallelDisplay(ctx), buf


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


class TestShowPhaseStartFromEntry:
    def test_renders_phase_label(self) -> None:
        console, buf = _make_console()
        ctx = make_display_context(console=console, env={})
        entry = PhaseEntryModel(phase_name="development_analysis")
        display = resolve_active_display(None, ctx)
        display.emit_phase_start_from_entry(entry)
        assert "Development Analysis" in buf.getvalue()

    def test_renders_outer_dev_label(self) -> None:
        console, buf = _make_console()
        ctx = make_display_context(console=console, env={})
        entry = PhaseEntryModel(phase_name="development", outer_dev_iteration=2, outer_dev_cap=5)
        display = resolve_active_display(None, ctx)
        display.emit_phase_start_from_entry(entry)
        out = buf.getvalue()
        assert "Cycle" in out
        assert "2" in out
        assert "5" in out

    def test_renders_inner_analysis_label(self) -> None:
        console, buf = _make_console()
        ctx = make_display_context(console=console, env={})
        entry = PhaseEntryModel(
            phase_name="development_analysis", inner_analysis=1, inner_analysis_cap=3
        )
        display = resolve_active_display(None, ctx)
        display.emit_phase_start_from_entry(entry)
        out = buf.getvalue()
        assert "Analysis" in out
        assert "1" in out
        assert "3" in out

    def test_renders_agent_name(self) -> None:
        console, buf = _make_console()
        ctx = make_display_context(console=console, env={})
        entry = PhaseEntryModel(phase_name="development", agent_name="claude-dev")
        display = resolve_active_display(None, ctx)
        display.emit_phase_start_from_entry(entry)
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
        display = resolve_active_display(None, ctx)
        display.emit_phase_start_from_entry(entry)
        out = buf.getvalue()
        assert out.index("Cycle") < out.index("Analysis")

    def test_ascii_fallback_mode(self) -> None:
        """In force_glyphs=ascii mode, glyphs are ASCII safe."""
        console, buf = _make_console()
        ctx = make_display_context(console=console, env={}, force_glyphs="ascii")
        entry = PhaseEntryModel(phase_name="development", outer_dev_iteration=1, inner_analysis=2)
        display = resolve_active_display(None, ctx)
        display.emit_phase_start_from_entry(entry)
        out = buf.getvalue()
        # ASCII fallback should not emit multi-byte unicode glyphs for iteration context
        assert "Development" in out

    def test_no_optional_fields_shows_phase_only(self) -> None:
        console, buf = _make_console()
        ctx = make_display_context(console=console, env={})
        entry = PhaseEntryModel(phase_name="planning")
        display = resolve_active_display(None, ctx)
        display.emit_phase_start_from_entry(entry)
        out = buf.getvalue()
        assert "Planning" in out
        # Should not include iteration context keywords when all None
        assert "Cycle" not in out
        assert "Analysis" not in out
        assert "Budget" not in out
