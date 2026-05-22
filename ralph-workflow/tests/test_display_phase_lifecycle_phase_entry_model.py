"""Black-box tests for ralph/display/phase_lifecycle.py.

Verifies the view-model dataclasses hold correct data, provide consistent
iteration labels, and convert cleanly to PhaseIterationContext for
downstream rendering.
"""

from __future__ import annotations

import pytest

from ralph.display.phase_lifecycle import PhaseEntryModel
from ralph.display.phase_status import format_analysis_cycle, format_dev_cycle
from ralph.pipeline.phase_transition import build_phase_entry_model_from_state
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    LoopCounterConfig,
    PhaseDefinition,
    PhaseLoopPolicy,
    PhaseTransition,
    PipelinePolicy,
)

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
        """Labels are ordered: outer_dev → inner_analysis."""
        m = PhaseEntryModel(
            phase_name="fix",
            outer_dev_iteration=2,
            inner_analysis=1,
            inner_analysis_cap=5,
        )
        parts = m.iteration_label_parts()
        assert parts[0] == format_dev_cycle(2)
        assert parts[1] == format_analysis_cycle(1, 5)

    def test_to_iteration_context_carries_all_fields(self) -> None:
        m = PhaseEntryModel(
            phase_name="development",
            outer_dev_iteration=1,
            inner_analysis=2,
            inner_analysis_cap=4,
        )
        ctx = m.to_iteration_context()
        assert ctx.outer_dev == 1
        assert ctx.inner_analysis == 2
        assert ctx.inner_analysis_cap == 4

    def test_to_iteration_context_has_context_when_any_field_set(self) -> None:
        m = PhaseEntryModel(phase_name="development", outer_dev_iteration=1)
        assert m.to_iteration_context().has_context()

    def test_to_iteration_context_no_context_when_all_none(self) -> None:
        m = PhaseEntryModel(phase_name="development")
        assert not m.to_iteration_context().has_context()

    def test_is_frozen(self) -> None:
        m = PhaseEntryModel(phase_name="development", outer_dev_iteration=1)
        with pytest.raises((AttributeError, TypeError)):
            m.__setattr__("outer_dev_iteration", 2)

    def test_commit_cleanup_entry_model_does_not_render_inner_analysis_context(self) -> None:
        policy = PipelinePolicy(
            phases={
                "development_commit_cleanup": PhaseDefinition(
                    drain="commit",
                    role="commit_cleanup",
                    loop_policy=PhaseLoopPolicy(
                        iteration_state_field="commit_cleanup_iteration"
                    ),
                    transitions=PhaseTransition(on_success="development_commit"),
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
                ),
            },
            entry_phase="development_commit_cleanup",
            terminal_phase="complete",
            loop_counters={
                "commit_cleanup_iteration": LoopCounterConfig(default_max=3),
            },
        )
        state = PipelineState(
            phase="development_commit_cleanup",
            loop_iterations={"commit_cleanup_iteration": 1},
            loop_caps={"commit_cleanup_iteration": 3},
        )

        entry = build_phase_entry_model_from_state(
            "development_commit_cleanup",
            state,
            policy,
        )

        assert entry.phase_role == "commit_cleanup"
        assert entry.inner_analysis is None
        assert entry.inner_analysis_cap is None
