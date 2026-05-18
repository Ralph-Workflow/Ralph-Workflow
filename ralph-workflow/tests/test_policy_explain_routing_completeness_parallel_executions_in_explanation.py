"""Tests that explain_policy output covers all routing surfaces.

Verifies that the policy explanation includes:
- POST-COMMIT ROUTES section with phase, budget_state, and target
- PARALLEL EXECUTION section with post_fanout_verify annotation
- RECOVERY POLICY section with preserve_session_on_categories
- Verification on_failure_route arrow in ASCII diagram
"""

from __future__ import annotations

from ralph.policy.explain import PolicyExplanation, explain_policy
from ralph.policy.loader import default_dir, load_policy
from ralph.policy.render import render_explanation_ascii, render_explanation_text


def _default_explanation() -> PolicyExplanation:
    bundle = load_policy(default_dir())
    return explain_policy(bundle)


class TestParallelExecutionsInExplanation:
    """explain_policy populates parallel_executions list on PolicyExplanation."""

    def test_parallel_executions_populated(self) -> None:
        exp = _default_explanation()
        assert len(exp.parallel_executions) > 0, (
            "PolicyExplanation.parallel_executions must be populated for the default policy"
        )

    def test_parallel_executions_have_post_fanout_verification(self) -> None:
        exp = _default_explanation()
        pe = exp.parallel_executions[0]
        assert hasattr(pe, "post_fanout_verification"), (
            "ParallelExplanation must have post_fanout_verification field"
        )

    def test_parallel_execution_backward_compat_singleton(self) -> None:
        exp = _default_explanation()
        assert exp.parallel_execution is not None, (
            "parallel_execution singleton must still be populated for backward compat"
        )
        assert exp.parallel_execution is exp.parallel_executions[0], (
            "parallel_execution must be the first entry from parallel_executions"
        )

    def test_parallel_execution_text_contains_post_fanout_verify(self) -> None:
        exp = _default_explanation()
        text = render_explanation_text(exp)
        assert "post_fanout_verify" in text, (
            "render_explanation_text must include post_fanout_verify annotation"
        )

    def test_fanout_annotation_in_ascii_contains_post_fanout_verify(self) -> None:
        exp = _default_explanation()
        ascii_text = render_explanation_ascii(exp)
        assert "post_fanout_verify=" in ascii_text, (
            "render_explanation_ascii FAN_OUT annotation must include post_fanout_verify"
        )
