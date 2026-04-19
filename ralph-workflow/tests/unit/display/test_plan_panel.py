"""Tests for PlanPanel."""

from __future__ import annotations

from datetime import UTC, datetime

from ralph.display.panels.plan import PlanPanel
from ralph.display.snapshot import DashboardSnapshot


def _make_snapshot(
    *,
    prompt_path: str | None = None,
    prompt_preview: tuple[str, ...] = (),
) -> DashboardSnapshot:
    return DashboardSnapshot(
        phase="planning",
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
        prompt_path=prompt_path,
        prompt_preview=prompt_preview,
        run_id=None,
        created_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
    )


class TestPlanPanelRender:
    def test_render_with_prompt_preview_shows_all_lines(self) -> None:
        panel = PlanPanel()
        snapshot = _make_snapshot(
            prompt_path="PROMPT.md",
            prompt_preview=("# title", "## Section", "Some content"),
        )
        result = panel.render(snapshot)
        assert result.title == "Plan: PROMPT.md"
        assert "# title" in result.renderable.plain
        assert "## Section" in result.renderable.plain
        assert "Some content" in result.renderable.plain

    def test_render_with_empty_prompt_preview_shows_no_preview_available(
        self,
    ) -> None:
        panel = PlanPanel()
        snapshot = _make_snapshot(prompt_path="PROMPT.md", prompt_preview=())
        result = panel.render(snapshot)
        assert result.title == "Plan: PROMPT.md"
        assert "no preview available" in result.renderable.plain

    def test_render_with_prompt_path_shows_escaped_path_in_title(self) -> None:
        panel = PlanPanel()
        snapshot = _make_snapshot(
            prompt_path="/path/with [brackets]/PROMPT.md",
            prompt_preview=("# title",),
        )
        result = panel.render(snapshot)
        assert result.title == "Plan: /path/with \\[brackets]/PROMPT.md"
