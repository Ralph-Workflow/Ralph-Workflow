"""Tests for PlanPanel."""

from __future__ import annotations

from datetime import UTC, datetime

from ralph.display.panels.plan import PlanPanel
from ralph.display.snapshot import DashboardSnapshot


def _make_snapshot(  # noqa: PLR0913 - test helper exposes many kwargs
    *,
    prompt_path: str | None = None,
    prompt_preview: tuple[str, ...] = (),
    plan_summary: str | None = None,
    plan_scope_items: tuple[str, ...] = (),
    plan_total_steps: int = 0,
    plan_current_step: int | None = None,
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
        plan_summary=plan_summary,
        plan_scope_items=plan_scope_items,
        plan_total_steps=plan_total_steps,
        plan_current_step=plan_current_step,
    )


class TestPlanPanelFallbackBehavior:
    def test_render_with_prompt_preview_shows_all_lines(self) -> None:
        panel = PlanPanel()
        snapshot = _make_snapshot(
            prompt_path="PROMPT.md",
            prompt_preview=("# title", "## Section", "Some content"),
        )
        result = panel.render(snapshot)
        assert result.title == "Plan · PROMPT.md"
        assert "# title" in result.renderable.plain
        assert "## Section" in result.renderable.plain
        assert "Some content" in result.renderable.plain

    def test_render_with_empty_preview_shows_fallback_placeholder(self) -> None:
        panel = PlanPanel()
        snapshot = _make_snapshot(prompt_path="PROMPT.md", prompt_preview=())
        result = panel.render(snapshot)
        assert result.title == "Plan · PROMPT.md"
        assert "no plan attached" in result.renderable.plain

    def test_render_with_prompt_path_shows_escaped_path_in_title(self) -> None:
        panel = PlanPanel()
        snapshot = _make_snapshot(
            prompt_path="/path/with [brackets]/PROMPT.md",
            prompt_preview=("# title",),
        )
        result = panel.render(snapshot)
        assert result.title == "Plan · /path/with \\[brackets]/PROMPT.md"

    def test_render_no_prompt_no_plan_shows_no_plan_attached(self) -> None:
        panel = PlanPanel()
        snapshot = _make_snapshot()
        result = panel.render(snapshot)
        assert result.title == "Plan"
        assert "no plan attached" in result.renderable.plain


class TestPlanPanelWithPlanArtifact:
    def test_render_with_summary_shows_context_label(self) -> None:
        panel = PlanPanel()
        snapshot = _make_snapshot(
            prompt_path="PROMPT.md",
            plan_summary="Implement foo and ship it",
            plan_scope_items=("Item one", "Item two"),
            plan_total_steps=5,
        )
        result = panel.render(snapshot)
        plain = result.renderable.plain
        assert "Context:" in plain
        assert "Implement foo and ship it" in plain
        assert "Item one" in plain
        assert "Item two" in plain
        assert "Steps:" in plain
        assert "5" in plain

    def test_render_truncates_scope_items_beyond_max(self) -> None:
        panel = PlanPanel()
        scope_items = tuple(f"Item {i}" for i in range(10))
        snapshot = _make_snapshot(
            plan_summary="Do many things",
            plan_scope_items=scope_items,
        )
        result = panel.render(snapshot)
        plain = result.renderable.plain
        assert "Item 0" in plain
        assert "Item 5" in plain
        assert "+4 more" in plain

    def test_render_with_current_step_shows_progress(self) -> None:
        panel = PlanPanel()
        snapshot = _make_snapshot(
            plan_summary="Plan x",
            plan_total_steps=10,
            plan_current_step=3,
        )
        result = panel.render(snapshot)
        plain = result.renderable.plain
        assert "Steps: 3/10" in plain

    def test_render_without_current_step_shows_em_dash(self) -> None:
        panel = PlanPanel()
        snapshot = _make_snapshot(
            plan_summary="Plan x",
            plan_total_steps=10,
            plan_current_step=None,
        )
        result = panel.render(snapshot)
        plain = result.renderable.plain
        assert "Steps: —/10" in plain
