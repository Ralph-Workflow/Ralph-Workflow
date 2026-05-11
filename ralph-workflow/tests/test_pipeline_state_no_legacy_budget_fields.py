"""Guard: PipelineState must not declare any of the removed legacy budget fields.

The following fields were identified as legacy hardcoded budget fields
that should no longer be first-class declared fields on PipelineState:
- total_iterations
- total_reviewer_passes
- development_budget_remaining
- review_budget_remaining
- budget_remaining    (replaced by derived get_budget_remaining() from budget_caps/outer_progress)
- iteration          (scalar mirror of outer_progress['iteration'])
- reviewer_pass      (scalar mirror of outer_progress['reviewer_pass'])

All of these were migrated away in favour of budget_caps / outer_progress dicts
keyed by policy-declared counter names. They remain handled in _migrate_legacy_state_fields
for checkpoint backward-compat, but must NOT be declared as Pydantic fields.
"""

from __future__ import annotations

from ralph.pipeline.state import PipelineState

FORBIDDEN_LEGACY_FIELDS = {
    "total_iterations",
    "total_reviewer_passes",
    "development_budget_remaining",
    "review_budget_remaining",
    "budget_remaining",
    "iteration",
    "reviewer_pass",
}


class TestNoLegacyBudgetFieldsOnPipelineState:
    """The four legacy budget cap/remaining fields must not be declared on PipelineState."""

    def test_total_iterations_not_declared(self) -> None:
        model_fields = set(PipelineState.model_fields.keys())
        assert "total_iterations" not in model_fields, (
            "PipelineState.total_iterations is a legacy hardcoded budget field "
            "that was removed. Use state.budget_caps['iteration'] or "
            "pipeline_policy.budget_counters['iteration'].default_max instead."
        )

    def test_total_reviewer_passes_not_declared(self) -> None:
        model_fields = set(PipelineState.model_fields.keys())
        assert "total_reviewer_passes" not in model_fields, (
            "PipelineState.total_reviewer_passes is a legacy hardcoded budget field "
            "that was removed. Use state.budget_caps['reviewer_pass'] or "
            "pipeline_policy.budget_counters['reviewer_pass'].default_max instead."
        )

    def test_development_budget_remaining_not_declared(self) -> None:
        model_fields = set(PipelineState.model_fields.keys())
        assert "development_budget_remaining" not in model_fields, (
            "PipelineState.development_budget_remaining is a legacy hardcoded field "
            "that was removed. Use state.get_budget_remaining('iteration') instead."
        )

    def test_review_budget_remaining_not_declared(self) -> None:
        model_fields = set(PipelineState.model_fields.keys())
        assert "review_budget_remaining" not in model_fields, (
            "PipelineState.review_budget_remaining is a legacy hardcoded field "
            "that was removed. Use state.get_budget_remaining('reviewer_pass') instead."
        )

    def test_budget_remaining_not_declared(self) -> None:
        model_fields = set(PipelineState.model_fields.keys())
        assert "budget_remaining" not in model_fields, (
            "PipelineState.budget_remaining is a legacy field that was removed. "
            "Use state.get_budget_remaining(counter) via budget_caps/outer_progress instead."
        )

    def test_no_forbidden_fields_declared(self) -> None:
        model_fields = set(PipelineState.model_fields.keys())
        violations = FORBIDDEN_LEGACY_FIELDS & model_fields
        assert not violations, (
            f"PipelineState declares forbidden legacy budget field(s): {sorted(violations)}. "
            "Remove them and use the generic budget_remaining/outer_progress dicts instead."
        )
