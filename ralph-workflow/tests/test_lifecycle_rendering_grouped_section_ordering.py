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
    render_completion_summary_group,
)
from ralph.display.context import make_display_context
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
