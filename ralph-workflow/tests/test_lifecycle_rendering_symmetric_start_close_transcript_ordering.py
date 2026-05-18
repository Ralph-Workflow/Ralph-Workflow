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
from ralph.display.phase_banner import show_phase_start_from_entry
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


class TestSymmetricStartCloseTranscriptOrdering:
    """Verify that phase-start and phase-close lines appear in the correct order
    and carry the same iteration context vocabulary."""

    def test_phase_start_before_phase_close_in_transcript(self) -> None:
        """[phase] snapshot line for a phase appears before its [phase-close] line.

        [phase] lines are emitted via emit_snapshot() when the phase changes;
        [phase-close] lines are emitted via emit_phase_close_from_exit().
        """
        renderer, buf = _make_renderer()
        # emit_snapshot triggers [phase] when phase changes
        snapshot = _blank_snapshot(phase="development", is_terminal_success=False)
        renderer.emit_snapshot(snapshot)
        renderer.begin_phase("development")
        entry = PhaseEntryModel(phase_name="development", phase_role="execution")
        exit_model = PhaseExitModel.from_entry_model(entry, ExitContext(exit_trigger="produced"))
        renderer.emit_phase_close_from_exit(exit_model)
        out = buf.getvalue()
        # phase start marker (emit_snapshot emits [phase]) appears before phase-close
        phase_pos = out.find("[phase]")
        close_pos = out.find("[phase-close]")
        assert phase_pos != -1, "Expected [phase] in transcript"
        assert close_pos != -1, "Expected [phase-close] in transcript"
        assert phase_pos < close_pos, "[phase] must appear before [phase-close]"

    def test_start_and_close_use_same_dev_label_vocabulary(self) -> None:
        """Phase-start banner and [phase-close] transcript line use the same Dev label."""



        # Phase-start banner
        start_buf = StringIO()
        start_console = Console(
            file=start_buf, force_terminal=False, highlight=False, color_system=None, width=200
        )
        start_ctx = make_display_context(console=start_console, env={})
        entry = PhaseEntryModel(
            phase_name="development",
            outer_dev_iteration=3,
            outer_dev_cap=5,
        )
        show_phase_start_from_entry(entry, display_context=start_ctx)

        # Phase-close transcript
        renderer, close_buf = _make_renderer()
        renderer.begin_phase("development")
        exit_model = PhaseExitModel.from_entry_model(entry, ExitContext(exit_trigger="produced"))
        renderer.emit_phase_close_from_exit(exit_model)

        start_out = start_buf.getvalue()
        close_out = close_buf.getvalue()

        # Both should reference Dev 3/5
        assert "Dev 3/5" in start_out, f"Expected 'Dev 3/5' in start banner: {start_out!r}"
        assert "Dev 3/5" in close_out, f"Expected 'Dev 3/5' in phase-close line: {close_out!r}"
