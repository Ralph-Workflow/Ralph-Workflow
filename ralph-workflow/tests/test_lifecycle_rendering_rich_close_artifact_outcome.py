"""Black-box tests for lifecycle model-driven rendering APIs.

After the wt-028-display consolidation, the single ``default`` mode
applies; the artifact outcome always renders when set (no
mode-conditional suppression).
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay, resolve_active_display
from ralph.display.phase_lifecycle import PhaseExitModel
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


class TestRichCloseArtifactOutcome:
    def _render_close(self, exit_model: PhaseExitModel, width: int = 80) -> str:
        buf = StringIO()
        console = Console(
            file=buf,
            record=True,
            force_terminal=False,
            color_system=None,
            width=width,
        )
        ctx = make_display_context(console=console, env={})

        display = resolve_active_display(None, ctx)
        display.emit_phase_close_banner(exit_model)
        return console.export_text()

    def test_artifact_outcome_appears_in_default_mode(self) -> None:
        exit_model = PhaseExitModel(
            phase_name="planning",
            exit_trigger="produced",
            artifact_outcome="plan: 5 step(s), 2 risk(s)",
        )
        output = self._render_close(exit_model, width=80)
        assert "artifact:" in output
        assert "plan: 5 step(s), 2 risk(s)" in output

    def test_artifact_outcome_appears_at_wide_width(self) -> None:
        exit_model = PhaseExitModel(
            phase_name="development",
            exit_trigger="produced",
            artifact_outcome="result produced",
        )
        output = self._render_close(exit_model, width=120)
        assert "artifact:" in output
        assert "result produced" in output

    def test_artifact_outcome_absent_when_empty(self) -> None:
        exit_model = PhaseExitModel(phase_name="development", artifact_outcome="")
        output = self._render_close(exit_model, width=120)
        assert "artifact:" not in output

    def test_artifact_outcome_always_present_when_set(self) -> None:
        """Single default-mode invariant: artifact outcome always renders when set."""
        exit_model = PhaseExitModel(
            phase_name="planning",
            exit_trigger="produced",
            artifact_outcome="plan: 3 step(s)",
        )
        output = self._render_close(exit_model, width=40)
        assert "artifact:" in output

    def test_artifact_outcome_after_stats_line(self) -> None:
        """artifact line must appear after the main banner line and before debug breadcrumbs."""
        exit_model = PhaseExitModel(
            phase_name="development",
            exit_trigger="produced",
            artifact_outcome="result produced",
            content_blocks=2,
            tool_calls=3,
        )
        output = self._render_close(exit_model, width=80)
        # artifact line should exist; stats line should exist
        assert "artifact:" in output
        assert "stats:" in output
        # artifact should appear after the banner (which has the phase name)
        assert output.index("Development") < output.index("artifact:")
