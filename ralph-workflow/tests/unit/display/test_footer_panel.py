"""Tests for FooterPanel."""

from __future__ import annotations

from datetime import UTC, datetime

from rich.text import Text

from ralph.display.panels.footer import FooterPanel, footer_panel
from ralph.display.snapshot import DashboardSnapshot


def _make_snapshot(*, phase: str = "development", run_id: str | None = None) -> DashboardSnapshot:
    return DashboardSnapshot(
        phase=phase,
        previous_phase=None,
        iteration=0,
        total_iterations=1,
        reviewer_pass=0,
        total_reviewer_passes=1,
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=None,
        pr_url=None,
        push_count=0,
        total_agent_calls=0,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path=None,
        prompt_preview=(),
        run_id=run_id,
        created_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
    )


def test_footer_panel_name() -> None:
    assert footer_panel.name == "footer"


def test_render_development_phase_shows_interrupt_hint() -> None:
    panel = FooterPanel()
    snapshot = _make_snapshot()

    result = panel.render(snapshot)

    assert isinstance(result, Text)
    assert "Ctrl+C: interrupt" in result.plain


def test_render_complete_phase_with_run_id_shows_log_path() -> None:
    panel = FooterPanel()
    snapshot = _make_snapshot(phase="complete", run_id="run-abc12345")

    result = panel.render(snapshot)

    assert isinstance(result, Text)
    assert "log: ~/.agent/logs/run-abc12345/" in result.plain


def test_render_width_under_80_shows_only_interrupt_hint() -> None:
    panel = FooterPanel()
    snapshot = _make_snapshot(phase="complete", run_id="run-abc12345")

    result = panel.render(snapshot, width=79)

    assert isinstance(result, Text)
    assert result.plain == "Ctrl+C: interrupt"
