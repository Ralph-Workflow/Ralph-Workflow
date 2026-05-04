"""Black-box tests for ralph/display/phase_lifecycle.py.

Verifies the view-model dataclasses hold correct data, provide consistent
iteration labels, and convert cleanly to PhaseIterationContext for
downstream rendering.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from ralph.display.phase_lifecycle import PhaseEntryModel, PhaseExitModel, RunCompletionModel
from ralph.display.phase_status import (
    format_analysis_cycle,
    format_budget_remaining,
    format_dev_cycle,
    format_fixer_cycle,
)
from ralph.display.snapshot import BudgetProgress

# ---------------------------------------------------------------------------
# PhaseEntryModel
# ---------------------------------------------------------------------------


class TestPhaseEntryModel:
    def test_defaults_are_none(self) -> None:
        m = PhaseEntryModel(phase_name="development")
        assert m.phase_role is None
        assert m.agent_name is None
        assert m.outer_dev_iteration is None
        assert m.inner_analysis is None
        assert m.inner_analysis_cap is None
        assert m.fixer_iteration is None
        assert m.budget_remaining is None

    def test_human_label_converts_underscores(self) -> None:
        assert PhaseEntryModel(phase_name="development_analysis").human_label() == (
            "Development Analysis"
        )
        assert PhaseEntryModel(phase_name="planning").human_label() == "Planning"

    def test_iteration_label_parts_empty_when_no_context(self) -> None:
        m = PhaseEntryModel(phase_name="development")
        assert m.iteration_label_parts() == []

    def test_iteration_label_parts_outer_dev_only(self) -> None:
        m = PhaseEntryModel(phase_name="development", outer_dev_iteration=3)
        parts = m.iteration_label_parts()
        assert len(parts) == 1
        assert parts[0] == format_dev_cycle(3)

    def test_iteration_label_parts_full_context_order(self) -> None:
        """Labels are ordered: outer_dev → inner_analysis → fixer → budget."""
        m = PhaseEntryModel(
            phase_name="fix",
            outer_dev_iteration=2,
            inner_analysis=1,
            inner_analysis_cap=5,
            fixer_iteration=1,
            budget_remaining=3,
        )
        parts = m.iteration_label_parts()
        assert parts[0] == format_dev_cycle(2)
        assert parts[1] == format_analysis_cycle(1, 5)
        assert parts[2] == format_fixer_cycle(1)
        assert parts[3] == format_budget_remaining(3)

    def test_to_iteration_context_carries_all_fields(self) -> None:
        m = PhaseEntryModel(
            phase_name="development",
            outer_dev_iteration=1,
            inner_analysis=2,
            inner_analysis_cap=4,
            fixer_iteration=1,
            budget_remaining=7,
        )
        ctx = m.to_iteration_context()
        assert ctx.outer_dev == 1
        assert ctx.inner_analysis == 2  # noqa: PLR2004
        assert ctx.inner_analysis_cap == 4  # noqa: PLR2004
        assert ctx.fixer == 1
        assert ctx.budget_remaining == 7  # noqa: PLR2004

    def test_to_iteration_context_has_context_when_any_field_set(self) -> None:
        m = PhaseEntryModel(phase_name="development", outer_dev_iteration=1)
        assert m.to_iteration_context().has_context()

    def test_to_iteration_context_no_context_when_all_none(self) -> None:
        m = PhaseEntryModel(phase_name="development")
        assert not m.to_iteration_context().has_context()

    def test_is_frozen(self) -> None:
        m = PhaseEntryModel(phase_name="development", outer_dev_iteration=1)
        try:
            m.outer_dev_iteration = 2  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
            raise AssertionError("should be frozen")
        except (AttributeError, TypeError):
            pass


# ---------------------------------------------------------------------------
# PhaseExitModel
# ---------------------------------------------------------------------------


class TestPhaseExitModel:
    def test_defaults(self) -> None:
        m = PhaseExitModel(phase_name="development")
        assert m.elapsed_seconds == 0.0
        assert m.exit_trigger is None
        assert m.content_blocks == 0
        assert m.thinking_blocks == 0
        assert m.tool_calls == 0
        assert m.errors == 0
        assert m.artifact_outcome == ""
        assert m.review_issues_found is None

    def test_exit_trigger_set(self) -> None:
        m = PhaseExitModel(phase_name="development", exit_trigger="produced")
        assert m.exit_trigger == "produced"

    def test_from_entry_model_copies_all_fields(self) -> None:
        entry = PhaseEntryModel(
            phase_name="fix",
            phase_role="fix",
            agent_name="claude",
            outer_dev_iteration=2,
            inner_analysis=1,
            inner_analysis_cap=3,
            fixer_iteration=1,
            budget_remaining=5,
            budget_counter_name="dev_budget",
        )
        exit_model = PhaseExitModel.from_entry_model(
            entry,
            elapsed_seconds=12.5,
            exit_trigger="produced",
            content_blocks=3,
            thinking_blocks=1,
            tool_calls=7,
            errors=0,
            artifact_outcome="fix: applied",
        )
        assert exit_model.phase_name == "fix"
        assert exit_model.phase_role == "fix"
        assert exit_model.agent_name == "claude"
        assert exit_model.outer_dev_iteration == 2  # noqa: PLR2004
        assert exit_model.inner_analysis == 1
        assert exit_model.inner_analysis_cap == 3  # noqa: PLR2004
        assert exit_model.fixer_iteration == 1
        assert exit_model.budget_remaining == 5  # noqa: PLR2004
        assert exit_model.budget_counter_name == "dev_budget"
        assert exit_model.elapsed_seconds == 12.5  # noqa: PLR2004
        assert exit_model.exit_trigger == "produced"
        assert exit_model.content_blocks == 3  # noqa: PLR2004
        assert exit_model.thinking_blocks == 1
        assert exit_model.tool_calls == 7  # noqa: PLR2004
        assert exit_model.errors == 0
        assert exit_model.artifact_outcome == "fix: applied"

    def test_to_iteration_context_reflects_entry_fields(self) -> None:
        m = PhaseExitModel(phase_name="fix", outer_dev_iteration=3, fixer_iteration=2)
        ctx = m.to_iteration_context()
        assert ctx.outer_dev == 3  # noqa: PLR2004
        assert ctx.fixer == 2  # noqa: PLR2004

    def test_is_frozen(self) -> None:
        m = PhaseExitModel(phase_name="development", elapsed_seconds=5.0)
        try:
            m.elapsed_seconds = 10.0  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
            raise AssertionError("should be frozen")
        except (AttributeError, TypeError):
            pass


# ---------------------------------------------------------------------------
# RunCompletionModel
# ---------------------------------------------------------------------------


class TestRunCompletionModel:
    def test_defaults(self) -> None:
        m = RunCompletionModel(final_phase="done", is_failure=False)
        assert m.exit_trigger == "exited"
        assert m.elapsed_seconds is None
        assert m.outer_dev_iteration is None
        assert m.fixer_iteration is None
        assert m.analysis_within_fixer is None
        assert m.total_agent_calls == 0
        assert m.content_blocks == 0
        assert m.thinking_blocks == 0
        assert m.tool_calls == 0
        assert m.errors == 0
        assert not m.review_issues_found
        assert m.last_error is None
        assert m.budget_progress == {}

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
            m.total_agent_calls = 5  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
            raise AssertionError("should be frozen")
        except (AttributeError, TypeError):
            pass

    def test_from_snapshot_extracts_fields(self) -> None:
        """from_snapshot correctly projects a PipelineSnapshot into the model."""
        snap = MagicMock()
        snap.phase = "done"
        snap.is_terminal_failure = False
        snap.outer_dev_iteration = 4
        snap.fixer_iteration = None
        snap.analysis_within_fixer = None
        snap.total_agent_calls = 10
        snap.review_issues_found = False
        snap.last_error = None
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
            content_blocks=5,
            thinking_blocks=2,
            tool_calls=15,
            errors=1,
        )

        assert model.final_phase == "done"
        assert not model.is_failure
        assert model.exit_trigger == "completed"
        assert model.elapsed_seconds == 42.0  # noqa: PLR2004
        assert model.outer_dev_iteration == 4  # noqa: PLR2004
        assert model.total_agent_calls == 10  # noqa: PLR2004
        assert model.content_blocks == 5  # noqa: PLR2004
        assert model.thinking_blocks == 2  # noqa: PLR2004
        assert model.tool_calls == 15  # noqa: PLR2004
        assert model.errors == 1
        assert not model.review_issues_found
        assert model.budget_progress == {"dev_cycles": (3, 10)}

    def test_from_snapshot_excludes_non_budget_counters(self) -> None:
        """Budget counters with tracks_budget=False are excluded."""
        snap = MagicMock()
        snap.phase = "done"
        snap.is_terminal_failure = False
        snap.outer_dev_iteration = None
        snap.fixer_iteration = None
        snap.analysis_within_fixer = None
        snap.total_agent_calls = 0
        snap.review_issues_found = False
        snap.last_error = None
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


# ---------------------------------------------------------------------------
# Cross-model consistency
# ---------------------------------------------------------------------------


def test_entry_iteration_label_parts_match_context_label_text() -> None:
    """iteration_label_parts() matches label text from to_iteration_context().context_labels()."""
    entry = PhaseEntryModel(
        phase_name="development",
        outer_dev_iteration=2,
        inner_analysis=1,
        inner_analysis_cap=4,
    )
    label_parts = entry.iteration_label_parts()
    ctx_labels = [label for label, _style in entry.to_iteration_context().context_labels()]
    assert label_parts == ctx_labels


def test_entry_exit_iteration_context_labels_are_consistent() -> None:
    """PhaseEntryModel and PhaseExitModel to_iteration_context() agree."""
    entry = PhaseEntryModel(
        phase_name="fix",
        outer_dev_iteration=3,
        fixer_iteration=2,
        budget_remaining=4,
    )
    exit_model = PhaseExitModel.from_entry_model(entry, elapsed_seconds=10.0)
    entry_labels = list(entry.to_iteration_context().context_labels())
    exit_labels = list(exit_model.to_iteration_context().context_labels())
    assert entry_labels == exit_labels
