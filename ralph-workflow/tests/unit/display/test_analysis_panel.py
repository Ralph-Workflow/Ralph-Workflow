"""Tests for AnalysisPanel."""

from __future__ import annotations

from datetime import UTC, datetime

from rich.console import Console
from rich.panel import Panel

from ralph.display.panels.analysis import AnalysisPanel
from ralph.display.snapshot import DashboardSnapshot


def _make_snapshot(
    *,
    analysis_phase: str | None = None,
    analysis_decision: str | None = None,
    analysis_reason: str | None = None,
) -> DashboardSnapshot:
    return DashboardSnapshot(
        phase="development_analysis",
        previous_phase="development",
        iteration=1,
        total_iterations=5,
        reviewer_pass=0,
        total_reviewer_passes=2,
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
        run_id=None,
        created_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
        analysis_phase=analysis_phase,
        analysis_decision=analysis_decision,
        analysis_reason=analysis_reason,
    )


def _render_plain(snapshot: DashboardSnapshot) -> str:
    panel = AnalysisPanel()
    rendered = panel.render(snapshot)
    assert isinstance(rendered, Panel)
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    console.print(rendered)
    return console.export_text()


def test_render_returns_panel_with_title() -> None:
    panel = AnalysisPanel()
    rendered = panel.render(_make_snapshot())
    assert rendered.title == "Analysis"


def test_render_empty_snapshot_shows_placeholder() -> None:
    text = _render_plain(_make_snapshot())
    assert "awaiting analysis" in text


def test_render_with_proceed_decision_shows_phase_and_decision() -> None:
    text = _render_plain(
        _make_snapshot(
            analysis_phase="development_analysis",
            analysis_decision="proceed",
            analysis_reason="everything green",
        )
    )
    assert "Development Analysis" in text
    assert "proceed" in text
    assert "everything green" in text


def test_render_with_revise_decision() -> None:
    text = _render_plain(
        _make_snapshot(
            analysis_phase="review_analysis",
            analysis_decision="revise",
            analysis_reason="nit fixes requested",
        )
    )
    assert "Review Analysis" in text
    assert "revise" in text


def test_render_truncates_long_reason() -> None:
    long_reason = "x" * 600
    text = _render_plain(
        _make_snapshot(
            analysis_phase="development_analysis",
            analysis_decision="proceed",
            analysis_reason=long_reason,
        )
    )
    assert "…" in text
