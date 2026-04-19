"""Tests for ResultsPanel."""

from __future__ import annotations

from datetime import UTC, datetime

from rich.console import Console
from rich.panel import Panel

from ralph.display.panels.results import results_panel
from ralph.display.snapshot import DashboardSnapshot


def _make_snapshot(
    *,
    phase: str = "development",
    last_error: str | None = None,
    pr_url: str | None = None,
) -> DashboardSnapshot:
    return DashboardSnapshot(
        phase=phase,
        previous_phase="planning",
        iteration=1,
        total_iterations=10,
        reviewer_pass=1,
        total_reviewer_passes=3,
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=last_error,
        pr_url=pr_url,
        push_count=2,
        total_agent_calls=7,
        total_continuations=3,
        total_fallbacks=1,
        total_retries=4,
        workers=(),
        prompt_path=None,
        prompt_preview=(),
        run_id=None,
        created_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
    )


def _render_str(panel: Panel) -> str:
    console = Console(force_terminal=False, width=200)
    with console.capture() as capture:
        console.print(panel)
    return capture.get()


def test_results_panel_name() -> None:
    assert results_panel.name == "results"


def test_render_development_phase_pending() -> None:
    rendered = _render_str(results_panel.render(_make_snapshot()))

    assert "results pending" in rendered


def test_render_complete_with_metrics() -> None:
    rendered = _render_str(results_panel.render(_make_snapshot(phase="complete")))

    assert "agent_calls" in rendered
    assert "continuations" in rendered
    assert "fallbacks" in rendered
    assert "retries" in rendered
    assert "push_count" in rendered
    assert "7" in rendered
    assert "3" in rendered
    assert "1" in rendered
    assert "4" in rendered
    assert "2" in rendered


def test_render_complete_with_pr_url() -> None:
    rendered = _render_str(
        results_panel.render(
            _make_snapshot(phase="complete", pr_url="https://example.com/pr/123"),
        ),
    )

    assert "https://example.com/pr/123" in rendered


def test_render_failed_with_error() -> None:
    rendered = _render_str(
        results_panel.render(
            _make_snapshot(phase="failed", last_error="boom: something broke"),
        ),
    )

    assert "boom: something broke" in rendered


def test_render_failed_unknown_error() -> None:
    rendered = _render_str(results_panel.render(_make_snapshot(phase="failed")))

    assert "unknown error" in rendered
