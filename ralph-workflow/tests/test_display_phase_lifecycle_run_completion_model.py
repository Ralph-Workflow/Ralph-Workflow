"""Black-box tests for ralph/display/phase_lifecycle.py.

Verifies the view-model dataclasses hold correct data, provide consistent
iteration labels, and convert cleanly to PhaseIterationContext for
downstream rendering.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ralph.display.auto_integrate_message import (
    OperationDisplayState,
    format_auto_integrate_message,
    rebase_outcome_to_operation_display_state,
)
from ralph.display.phase_lifecycle import (
    PhaseActivityCounts,
    RunCompletionModel,
)
from ralph.display.snapshot import BudgetProgress
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline.rebase_state import RebaseState

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


def test_integrating_rendered_prefix() -> None:
    msg = format_auto_integrate_message(
        operation_display_state=OperationDisplayState.INTEGRATING, target="main"
    )
    assert msg.startswith("integrating")


def test_landing_rendered_prefix() -> None:
    msg = format_auto_integrate_message(
        operation_display_state=OperationDisplayState.LANDING, target="main"
    )
    assert msg.startswith("landing")


def test_publishing_rendered_prefix() -> None:
    msg = format_auto_integrate_message(
        operation_display_state=OperationDisplayState.PUBLISHING, target="main"
    )
    assert msg.startswith("publishing")


def test_conflict_resolution_rendered_prefix() -> None:
    msg = format_auto_integrate_message(
        operation_display_state=OperationDisplayState.CONFLICT_RESOLUTION, target="main"
    )
    assert msg.startswith("conflict resolution")


def test_evidence_diagnosis_rendered_prefix() -> None:
    msg = format_auto_integrate_message(
        operation_display_state=OperationDisplayState.EVIDENCE_DIAGNOSIS, target="main"
    )
    assert msg.startswith("evidence diagnosis")


def test_waiting_rendered_prefix() -> None:
    msg = format_auto_integrate_message(
        operation_display_state=OperationDisplayState.WAITING, target="main"
    )
    assert msg.startswith("waiting")


def test_intervention_required_rendered_prefix() -> None:
    msg = format_auto_integrate_message(
        operation_display_state=OperationDisplayState.INTERVENTION_REQUIRED, target="main"
    )
    assert msg.startswith("escalating through the autonomous fallback ladder")


def test_terminal_rendered_prefix() -> None:
    msg = format_auto_integrate_message(
        operation_display_state=OperationDisplayState.TERMINAL, target="main"
    )
    assert msg.startswith("terminal")


@pytest.mark.parametrize("state", list(OperationDisplayState))
def test_all_operation_display_states_rendered_prefix(state: OperationDisplayState) -> None:
    """Every OperationDisplayState value formats with its canonical prefix."""
    msg = format_auto_integrate_message(operation_display_state=state, target="main")
    if state is OperationDisplayState.INTERVENTION_REQUIRED:
        expected_prefix = "escalating through the autonomous fallback ladder"
    else:
        expected_prefix = state.value.replace("_", " ")
    assert msg.startswith(expected_prefix)


@pytest.mark.parametrize("kind", list(RebaseOutcomeKind))
def test_all_rebase_outcome_kinds_project_and_render_prefix(kind: RebaseOutcomeKind) -> None:
    """Every RebaseOutcomeKind value projects to OperationDisplayState and renders its prefix."""
    display_state = rebase_outcome_to_operation_display_state(kind)
    assert display_state is not None
    assert isinstance(display_state, OperationDisplayState)
    msg = format_auto_integrate_message(last_outcome=kind, target="main")
    if display_state is OperationDisplayState.INTERVENTION_REQUIRED:
        expected_prefix = "escalating through the autonomous fallback ladder"
    else:
        expected_prefix = display_state.value.replace("_", " ")
    assert msg.startswith(expected_prefix)


def test_rebase_state_object_outcome_projection() -> None:
    """rebase_outcome_to_operation_display_state projects directly from a RebaseState object."""
    state_landing = RebaseState(last_outcome=RebaseOutcomeKind.LANDING)
    assert rebase_outcome_to_operation_display_state(state_landing) == OperationDisplayState.LANDING

    state_conflict = RebaseState(last_outcome=RebaseOutcomeKind.CONFLICT)
    assert (
        rebase_outcome_to_operation_display_state(state_conflict)
        == OperationDisplayState.CONFLICT_RESOLUTION
    )

    state_unknown = RebaseState(last_outcome=RebaseOutcomeKind.UNKNOWN)
    assert (
        rebase_outcome_to_operation_display_state(state_unknown)
        == OperationDisplayState.INTERVENTION_REQUIRED
    )


def test_format_auto_integrate_message_with_rebase_object() -> None:
    """format_auto_integrate_message accepts rebase object and renders typed prefix."""
    state_integrating = RebaseState(last_outcome=RebaseOutcomeKind.INTEGRATING)
    msg_integrating = format_auto_integrate_message(rebase=state_integrating, target="main")
    assert msg_integrating.startswith("integrating")

    state_waiting = RebaseState(last_outcome=RebaseOutcomeKind.WAITING)
    msg_waiting = format_auto_integrate_message(rebase=state_waiting, target="main")
    assert msg_waiting.startswith("waiting")

    state_unknown = RebaseState(last_outcome=RebaseOutcomeKind.UNKNOWN)
    msg_unknown = format_auto_integrate_message(rebase=state_unknown, target="main")
    assert msg_unknown.startswith("escalating through the autonomous fallback ladder")
