"""Tests for DecisionLogPanel."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rich.console import Console
from rich.panel import Panel

from ralph.display.panels.decision_log import DecisionLogPanel
from ralph.display.snapshot import DashboardSnapshot


def _make_snapshot(
    *,
    decision_log: tuple[tuple[str, str, str, str], ...] = (),
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
        decision_log=decision_log,
    )


def _render_plain(snapshot: DashboardSnapshot) -> str:
    panel = DecisionLogPanel()
    rendered = panel.render(snapshot)
    assert isinstance(rendered, Panel)
    console = Console(record=True, width=140, force_terminal=False, color_system=None)
    console.print(rendered)
    return console.export_text()


def test_render_returns_panel_with_title() -> None:
    panel = DecisionLogPanel()
    rendered = panel.render(_make_snapshot())
    assert rendered.title == "Decision Log"


def test_render_empty_shows_placeholder() -> None:
    text = _render_plain(_make_snapshot())
    assert "no decisions yet" in text


def test_render_single_decision() -> None:
    now = datetime.now(UTC).isoformat()
    text = _render_plain(
        _make_snapshot(
            decision_log=(("development_analysis", "proceed", "looks good", now),),
        )
    )
    assert "Development Analysis" in text
    assert "proceed" in text
    assert "looks good" in text


def test_render_truncates_to_max_rows() -> None:
    now = datetime.now(UTC)
    # Build 12 entries; only the latest 6 should render.
    entries: list[tuple[str, str, str, str]] = []
    for i in range(12):
        ts = (now - timedelta(minutes=12 - i)).isoformat()
        entries.append(("development_analysis", "proceed", f"reason-{i}", ts))
    snapshot = _make_snapshot(decision_log=tuple(entries))
    text = _render_plain(snapshot)
    assert "reason-11" in text
    assert "reason-0" not in text


def test_render_includes_relative_time_label() -> None:
    now = datetime.now(UTC).isoformat()
    text = _render_plain(
        _make_snapshot(
            decision_log=(("development_analysis", "proceed", "ok", now),),
        )
    )
    assert "ago" in text or "now" in text
