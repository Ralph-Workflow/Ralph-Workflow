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
