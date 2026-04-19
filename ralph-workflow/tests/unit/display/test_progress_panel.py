"""Tests for progress panel."""

from __future__ import annotations

from unittest.mock import MagicMock

from rich.panel import Panel

from ralph.display.panels.progress import ProgressPanel, progress_panel
from ralph.display.snapshot import DashboardSnapshot
from ralph.display.theme import make_console


class TestProgressPanel:
    def test_name(self):
        assert ProgressPanel().name == "progress"

    def test_render_shows_iteration_progress(self):
        panel = ProgressPanel()
        snapshot = MagicMock(spec=DashboardSnapshot)
        snapshot.total_iterations = 10
        snapshot.iteration = 3
        snapshot.total_reviewer_passes = 2
        snapshot.reviewer_pass = 1

        result = panel.render(snapshot)

        assert isinstance(result, Panel)
        assert result.title == "Progress"
        console = make_console()
        with console.capture() as capture:
            console.print(result)
        output = capture.get()
        assert "Development" in output
        assert "Review" in output

    def test_render_zero_iterations_shows_no_budget(self):
        panel = ProgressPanel()
        snapshot = MagicMock(spec=DashboardSnapshot)
        snapshot.total_iterations = 0
        snapshot.iteration = 0
        snapshot.total_reviewer_passes = 0
        snapshot.reviewer_pass = 0

        result = panel.render(snapshot)

        assert isinstance(result, Panel)
        assert "no iteration budget" in result.renderable.plain


class TestProgressPanelSingleton:
    def test_progress_panel_is_singleton(self):
        assert progress_panel is not None
        assert isinstance(progress_panel, ProgressPanel)
