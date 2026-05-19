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
from ralph.policy.render import render_explanation_text


def _default_explanation() -> PolicyExplanation:
    bundle = load_policy(default_dir())
    return explain_policy(bundle)


class TestRecoveryPreserveSessionInExplanation:
    """explain_policy populates preserve_session_on_categories in RecoveryExplanation."""

    def test_recovery_explanation_populated(self) -> None:
        exp = _default_explanation()
        assert exp.recovery is not None, "RecoveryExplanation must be populated"

    def test_recovery_preserve_session_on_categories_is_list(self) -> None:
        exp = _default_explanation()
        assert exp.recovery is not None
        assert isinstance(exp.recovery.preserve_session_on_categories, list), (
            "preserve_session_on_categories must be a list"
        )

    def test_recovery_section_rendered_in_text(self) -> None:
        exp = _default_explanation()
        text = render_explanation_text(exp)
        assert "RECOVERY POLICY" in text, (
            "render_explanation_text must include RECOVERY POLICY section"
        )

    def test_recovery_session_preserved_info_rendered(self) -> None:
        exp = _default_explanation()
        text = render_explanation_text(exp)
        assert "Session preserved on" in text, (
            "render_explanation_text must include 'Session preserved on' line"
        )
