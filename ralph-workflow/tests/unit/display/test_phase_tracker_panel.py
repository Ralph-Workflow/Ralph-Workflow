"""Tests for PhaseTrackerPanel."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from rich.panel import Panel

from ralph.display.panels.phase_tracker import KNOWN_PHASES, PhaseTrackerPanel
from ralph.display.snapshot import DashboardSnapshot
from ralph.display.theme import RALPH_THEME

ITERATION = 3
TOTAL_ITERATIONS = 10
TOTAL_REVIEWER_PASSES = 4


def _make_snapshot(  # noqa: PLR0913
    *,
    phase: str = "development",
    interrupted_by_user: bool = False,
    last_error: str | None = None,
    active_agent: str | None = None,
    active_tool: str | None = None,
    active_path: str | None = None,
    active_workdir: str | None = None,
    active_command: str | None = None,
    last_activity_line: str | None = None,
) -> DashboardSnapshot:
    return DashboardSnapshot(
        phase=phase,
        previous_phase="planning",
        iteration=ITERATION,
        total_iterations=TOTAL_ITERATIONS,
        reviewer_pass=1,
        total_reviewer_passes=TOTAL_REVIEWER_PASSES,
        review_issues_found=True,
        interrupted_by_user=interrupted_by_user,
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
        created_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
        active_agent=active_agent,
        active_tool=active_tool,
        active_path=active_path,
        active_workdir=active_workdir,
        active_command=active_command,
        last_activity_line=last_activity_line,
    )


class TestPhaseTrackerPanel:
    """Tests for PhaseTrackerPanel."""

    @pytest.fixture
    def panel(self) -> PhaseTrackerPanel:
        return PhaseTrackerPanel()

    def test_name_attribute(self, panel: PhaseTrackerPanel) -> None:
        assert panel.name == "phase_tracker"

    def test_development_phase_shows_development_in_output(
        self,
        panel: PhaseTrackerPanel,
    ) -> None:
        snapshot = _make_snapshot(phase="development")
        rendered = panel.render(snapshot)
        assert isinstance(rendered, Panel)
        content_text = rendered.renderable if hasattr(rendered, "renderable") else str(rendered)
        content_str = str(content_text)
        assert "Development" in content_str

    def test_failed_phase_shows_error_message(
        self,
        panel: PhaseTrackerPanel,
    ) -> None:
        snapshot = _make_snapshot(phase="failed", last_error="boom")
        rendered = panel.render(snapshot)
        content_text = rendered.renderable if hasattr(rendered, "renderable") else str(rendered)
        content_str = str(content_text)
        assert "✗" in content_str
        assert "boom" in content_str

    def test_interrupted_shows_warning(
        self,
        panel: PhaseTrackerPanel,
    ) -> None:
        snapshot = _make_snapshot(interrupted_by_user=True)
        rendered = panel.render(snapshot)
        content_text = rendered.renderable if hasattr(rendered, "renderable") else str(rendered)
        content_str = str(content_text)
        assert "INTERRUPTED" in content_str

    def test_iteration_count_displayed(
        self,
        panel: PhaseTrackerPanel,
    ) -> None:
        snapshot = _make_snapshot()
        rendered = panel.render(snapshot)
        content_text = rendered.renderable if hasattr(rendered, "renderable") else str(rendered)
        content_str = str(content_text)
        assert f"Iteration {ITERATION}/{TOTAL_ITERATIONS}" in content_str

    def test_review_pass_displayed_when_active(
        self,
        panel: PhaseTrackerPanel,
    ) -> None:
        snapshot = _make_snapshot()
        rendered = panel.render(snapshot)
        content_text = rendered.renderable if hasattr(rendered, "renderable") else str(rendered)
        content_str = str(content_text)
        assert f"Review 1/{TOTAL_REVIEWER_PASSES}" in content_str

    def test_review_pass_not_shown_when_zero(
        self,
        panel: PhaseTrackerPanel,
    ) -> None:
        snapshot = DashboardSnapshot(
            phase="development",
            previous_phase="planning",
            iteration=ITERATION,
            total_iterations=TOTAL_ITERATIONS,
            reviewer_pass=0,
            total_reviewer_passes=TOTAL_REVIEWER_PASSES,
            review_issues_found=True,
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
        )
        rendered = panel.render(snapshot)
        content_text = rendered.renderable if hasattr(rendered, "renderable") else str(rendered)
        content_str = str(content_text)
        assert "Review" not in content_str

    def test_long_error_truncated(
        self,
        panel: PhaseTrackerPanel,
    ) -> None:
        long_error = "x" * 300
        snapshot = _make_snapshot(phase="failed", last_error=long_error)
        rendered = panel.render(snapshot)
        content_text = rendered.renderable if hasattr(rendered, "renderable") else str(rendered)
        content_str = str(content_text)
        assert len(content_str) < 300 + 50
        assert "✗" in content_str

    def test_phase_tracker_renders_activity_context_when_present(
        self,
        panel: PhaseTrackerPanel,
    ) -> None:
        snapshot = _make_snapshot(
            active_agent="developer",
            active_tool="edit_file",
            active_path="src/foo.py",
            active_workdir="/tmp/project",
            active_command="python -m pytest tests/test_foo.py",
            last_activity_line="Editing foo.py to expose plan progress",
        )
        rendered = panel.render(snapshot)
        content_text = rendered.renderable if hasattr(rendered, "renderable") else str(rendered)
        content_str = str(content_text)
        assert "Agent: developer" in content_str
        assert "Tool: edit_file" in content_str
        assert "Path: src/foo.py" in content_str
        assert "Workdir: /tmp/project" in content_str
        assert "Command: python -m pytest tests/test_foo.py" in content_str
        assert "Editing foo.py to expose plan progress" in content_str

    def test_phase_name_formatted_correctly(
        self,
        panel: PhaseTrackerPanel,
    ) -> None:
        for phase in KNOWN_PHASES:
            snapshot = _make_snapshot(phase=phase)
            rendered = panel.render(snapshot)
            content_text = rendered.renderable if hasattr(rendered, "renderable") else str(rendered)
            content_str = str(content_text)
            expected = phase.replace("_", " ").title()
            assert expected in content_str, f"Expected '{expected}' in output for phase '{phase}'"

    def test_render_returns_panel_with_correct_border(
        self,
        panel: PhaseTrackerPanel,
    ) -> None:
        snapshot = _make_snapshot()
        rendered = panel.render(snapshot)
        assert isinstance(rendered, Panel)
        assert rendered.title == "Phase"

    def test_render_with_custom_width(
        self,
        panel: PhaseTrackerPanel,
    ) -> None:
        snapshot = _make_snapshot()
        rendered = panel.render(snapshot, width=60)
        assert isinstance(rendered, Panel)

    def test_render_with_custom_theme(
        self,
        panel: PhaseTrackerPanel,
    ) -> None:
        snapshot = _make_snapshot()
        rendered = panel.render(snapshot, theme=RALPH_THEME)
        assert isinstance(rendered, Panel)
