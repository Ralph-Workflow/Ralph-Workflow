"""Tests for HeaderPanel."""

from __future__ import annotations

from datetime import UTC, datetime

from rich.panel import Panel

from ralph.display.panels.header import header_panel
from ralph.display.snapshot import DashboardSnapshot


def _make_snapshot(
    *,
    prompt_path: str | None = None,
    run_id: str | None = None,
) -> DashboardSnapshot:
    return DashboardSnapshot(
        phase="development",
        previous_phase="planning",
        iteration=1,
        total_iterations=10,
        reviewer_pass=1,
        total_reviewer_passes=3,
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
        prompt_path=prompt_path,
        prompt_preview=(),
        run_id=run_id,
        created_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
    )


def test_header_panel_name() -> None:
    assert header_panel.name == "header"


def test_render_with_prompt_path_and_run_id_shows_both() -> None:
    snapshot = _make_snapshot(
        prompt_path="/path/to/PROMPT.md",
        run_id="run-abc12345",
    )
    panel = header_panel.render(snapshot)

    assert isinstance(panel, Panel)
    rendered = panel.renderable
    assert "run: run-abc1…" in str(rendered)
    assert "/path/to/PROMPT.md" in str(rendered)


def test_render_with_prompt_path_none_shows_no_plan_attached() -> None:
    snapshot = _make_snapshot(prompt_path=None, run_id=None)
    panel = header_panel.render(snapshot)

    assert isinstance(panel, Panel)
    rendered = panel.renderable
    assert "no plan attached" in str(rendered)


def test_render_with_prompt_path_containing_brackets_shows_escaped() -> None:
    snapshot = _make_snapshot(
        prompt_path="/path/to/[red]alert].md",
        run_id=None,
    )
    panel = header_panel.render(snapshot)

    assert isinstance(panel, Panel)
    rendered = panel.renderable
    assert "\\[red]" in str(rendered)
