"""Black-box tests for ralph/display/phase_lifecycle.py.

Verifies the view-model dataclasses hold correct data, provide consistent
iteration labels, and convert cleanly to PhaseIterationContext for
downstream rendering.
"""

from __future__ import annotations

from ralph.display.phase_lifecycle import (
    ExitContext,
    PhaseEntryModel,
    PhaseExitModel,
)

# ---------------------------------------------------------------------------
# PhaseEntryModel
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
        assert m.waiting_status_line is None
        assert m.last_failure_category is None

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
        )
        exit_model = PhaseExitModel.from_entry_model(
            entry,
            ExitContext(
                elapsed_seconds=12.5,
                exit_trigger="produced",
                content_blocks=3,
                thinking_blocks=1,
                tool_calls=7,
                errors=0,
                artifact_outcome="fix: applied",
                waiting_status_line="waiting for child",
                last_failure_category="timeout",
            ),
        )
        assert exit_model.phase_name == "fix"
        assert exit_model.phase_role == "fix"
        assert exit_model.agent_name == "claude"
        assert exit_model.outer_dev_iteration == 2
        assert exit_model.inner_analysis == 1
        assert exit_model.inner_analysis_cap == 3
        assert exit_model.elapsed_seconds == 12.5
        assert exit_model.exit_trigger == "produced"
        assert exit_model.content_blocks == 3
        assert exit_model.thinking_blocks == 1
        assert exit_model.tool_calls == 7
        assert exit_model.errors == 0
        assert exit_model.artifact_outcome == "fix: applied"
        assert exit_model.waiting_status_line == "waiting for child"
        assert exit_model.last_failure_category == "timeout"

    def test_to_iteration_context_reflects_entry_fields(self) -> None:
        m = PhaseExitModel(phase_name="fix", outer_dev_iteration=3)
        ctx = m.to_iteration_context()
        assert ctx.outer_dev == 3

    def test_is_frozen(self) -> None:
        m = PhaseExitModel(phase_name="development", elapsed_seconds=5.0)
        try:
            m.elapsed_seconds = 10.0
            raise AssertionError("should be frozen")
        except (AttributeError, TypeError):
            pass
