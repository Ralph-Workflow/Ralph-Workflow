"""Black-box tests for ralph/display/phase_lifecycle.py.

Verifies the view-model dataclasses hold correct data, provide consistent
iteration labels, and convert cleanly to PhaseIterationContext for
downstream rendering.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from ralph.display.phase_lifecycle import (
    PhaseActivityCounts,
    RunCompletionModel,
)
from ralph.display.snapshot import BudgetProgress

# ---------------------------------------------------------------------------
# PhaseEntryModel
# ---------------------------------------------------------------------------


class TestRunCompletionModel:
    def test_defaults(self) -> None:
        m = RunCompletionModel(final_phase="done", is_failure=False)
        assert m.exit_trigger == "exited"
        assert m.elapsed_seconds is None
        assert m.outer_dev_iteration is None
        assert m.total_agent_calls == 0
        assert m.content_blocks == 0
        assert m.thinking_blocks == 0
        assert m.tool_calls == 0
        assert m.errors == 0
        assert not m.review_issues_found
        assert m.last_error is None
        assert m.budget_progress == {}
        assert m.waiting_status_line is None
        assert m.last_failure_category is None

    def test_failure_flag(self) -> None:
        m = RunCompletionModel(final_phase="failed", is_failure=True, exit_trigger="failed")
        assert m.is_failure
        assert m.exit_trigger == "failed"

    def test_budget_progress_stored(self) -> None:
        m = RunCompletionModel(
            final_phase="done",
            is_failure=False,
            budget_progress={"dev_cycles": (3, 10)},
        )
        assert m.budget_progress["dev_cycles"] == (3, 10)

    def test_is_frozen(self) -> None:
        m = RunCompletionModel(final_phase="done", is_failure=False)
        try:
            m.total_agent_calls = 5
            raise AssertionError("should be frozen")
        except (AttributeError, TypeError):
            pass

    def test_from_snapshot_extracts_fields(self) -> None:
        """from_snapshot correctly projects a PipelineSnapshot into the model."""
        snap = MagicMock()
        snap.phase = "done"
        snap.is_terminal_failure = False
        snap.outer_dev_iteration = 4
        snap.total_agent_calls = 10
        snap.review_issues_found = False
        snap.last_error = None
        snap.decision_log = ()
        snap.last_activity_line = None
        snap.waiting_status_line = None
        snap.last_failure_category = None
        snap.budget_progress = {
            "dev_cycles": BudgetProgress(
                completed=3,
                cap=10,
                description="dev_cycles",
                tracks_budget=True,
            ),
        }

        model = RunCompletionModel.from_snapshot(
            snap,
            exit_trigger="completed",
            elapsed_seconds=42.0,
            activity=PhaseActivityCounts(
                content_blocks=5, thinking_blocks=2, tool_calls=15, errors=1
            ),
        )

        assert model.final_phase == "done"
        assert not model.is_failure
        assert model.exit_trigger == "completed"
        assert model.elapsed_seconds == 42.0
        assert model.outer_dev_iteration == 4
        assert model.total_agent_calls == 10
        assert model.content_blocks == 5
        assert model.thinking_blocks == 2
        assert model.tool_calls == 15
        assert model.errors == 1
        assert not model.review_issues_found
        assert model.budget_progress == {"dev_cycles": (3, 10)}

    def test_from_snapshot_excludes_non_budget_counters(self) -> None:
        """Budget counters with tracks_budget=False are excluded."""
        snap = MagicMock()
        snap.phase = "done"
        snap.is_terminal_failure = False
        snap.outer_dev_iteration = None
        snap.total_agent_calls = 0
        snap.review_issues_found = False
        snap.last_error = None
        snap.decision_log = ()
        snap.last_activity_line = None
        snap.waiting_status_line = None
        snap.last_failure_category = None
        snap.budget_progress = {
            "tracked": BudgetProgress(
                completed=1, cap=5, description="tracked", tracks_budget=True
            ),
            "untracked": BudgetProgress(
                completed=2, cap=3, description="untracked", tracks_budget=False
            ),
            "zero_cap": BudgetProgress(
                completed=0, cap=0, description="zero_cap", tracks_budget=True
            ),
        }

        model = RunCompletionModel.from_snapshot(snap, exit_trigger="completed")
        assert "tracked" in model.budget_progress
        assert "untracked" not in model.budget_progress
        assert "zero_cap" not in model.budget_progress

    def test_analysis_decisions_defaults_empty(self) -> None:
        m = RunCompletionModel(final_phase="done", is_failure=False)
        assert m.analysis_decisions == ()

    def test_last_activity_line_defaults_none(self) -> None:
        m = RunCompletionModel(final_phase="done", is_failure=False)
        assert m.last_activity_line is None

    def test_from_snapshot_extracts_analysis_decisions(self) -> None:
        """from_snapshot filters analysis phases from decision_log."""
        snap = MagicMock()
        snap.phase = "done"
        snap.is_terminal_failure = False
        snap.outer_dev_iteration = None
        snap.total_agent_calls = 0
        snap.review_issues_found = False
        snap.last_error = None
        snap.last_activity_line = None
        snap.waiting_status_line = None
        snap.last_failure_category = None
        snap.decision_log = (
            ("development_analysis", "proceed", "tests green", "2026-01-01T00:00:00"),
            ("commit", "complete", "committed", "2026-01-01T00:01:00"),
            ("review_analysis", "revise", "nit fixes", "2026-01-01T00:02:00"),
        )
        snap.budget_progress = {}

        model = RunCompletionModel.from_snapshot(snap, exit_trigger="completed")

        assert len(model.analysis_decisions) == 2
        assert model.analysis_decisions[0] == ("development_analysis", "proceed", "tests green")
        assert model.analysis_decisions[1] == ("review_analysis", "revise", "nit fixes")

    def test_from_snapshot_extracts_last_activity_line(self) -> None:
        """from_snapshot carries last_activity_line from snapshot."""
        snap = MagicMock()
        snap.phase = "done"
        snap.is_terminal_failure = False
        snap.outer_dev_iteration = None
        snap.total_agent_calls = 0
        snap.review_issues_found = False
        snap.last_error = None
        snap.decision_log = ()
        snap.last_activity_line = "read file: src/main.py"
        snap.waiting_status_line = None
        snap.last_failure_category = None
        snap.budget_progress = {}

        model = RunCompletionModel.from_snapshot(snap, exit_trigger="completed")
        assert model.last_activity_line == "read file: src/main.py"

    def test_from_snapshot_extracts_waiting_status_and_failure_category(self) -> None:
        """from_snapshot carries waiting_status_line and last_failure_category."""
        snap = MagicMock()
        snap.phase = "done"
        snap.is_terminal_failure = False
        snap.outer_dev_iteration = None
        snap.total_agent_calls = 0
        snap.review_issues_found = False
        snap.last_error = None
        snap.decision_log = ()
        snap.last_activity_line = None
        snap.waiting_status_line = "waiting for child process"
        snap.last_failure_category = "timeout"
        snap.budget_progress = {}

        model = RunCompletionModel.from_snapshot(snap, exit_trigger="failed")
        assert model.waiting_status_line == "waiting for child process"
        assert model.last_failure_category == "timeout"

    def test_from_snapshot_analysis_decisions_empty_when_no_analysis_phases(self) -> None:
        """from_snapshot yields empty analysis_decisions when log has no analysis phases."""
        snap = MagicMock()
        snap.phase = "done"
        snap.is_terminal_failure = False
        snap.outer_dev_iteration = None
        snap.total_agent_calls = 0
        snap.review_issues_found = False
        snap.last_error = None
        snap.last_activity_line = None
        snap.waiting_status_line = None
        snap.last_failure_category = None
        snap.decision_log = (("commit", "complete", "committed", "2026-01-01T00:01:00"),)
        snap.budget_progress = {}

        model = RunCompletionModel.from_snapshot(snap, exit_trigger="completed")
        assert model.analysis_decisions == ()

    def test_mcp_restart_count_defaults_zero(self) -> None:
        """RunCompletionModel.mcp_restart_count defaults to zero."""
        m = RunCompletionModel(final_phase="done", is_failure=False)
        assert m.mcp_restart_count == 0

    def test_from_snapshot_extracts_mcp_restart_count(self) -> None:
        """from_snapshot carries mcp_restart_count from snapshot."""
        snap = MagicMock()
        snap.phase = "done"
        snap.is_terminal_failure = False
        snap.outer_dev_iteration = None
        snap.total_agent_calls = 0
        snap.review_issues_found = False
        snap.last_error = None
        snap.decision_log = ()
        snap.last_activity_line = None
        snap.waiting_status_line = None
        snap.last_failure_category = None
        snap.mcp_restart_count = 2
        snap.budget_progress = {}

        model = RunCompletionModel.from_snapshot(snap, exit_trigger="completed")
        assert model.mcp_restart_count == 2
