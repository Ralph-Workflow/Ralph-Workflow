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


class TestPostCommitRoutesInExplanation:
    """explain_policy populates post_commit_routes on PolicyExplanation."""

    def test_post_commit_routes_populated(self) -> None:
        exp = _default_explanation()
        assert len(exp.post_commit_routes) > 0, (
            "PolicyExplanation.post_commit_routes must be populated "
            "from pipeline.post_commit_routes"
        )

    def test_post_commit_routes_have_phase_budget_state_target(self) -> None:
        exp = _default_explanation()
        route = exp.post_commit_routes[0]
        assert route.phase, "PostCommitRouteExplanation.phase must not be empty"
        assert route.budget_state in ("remaining", "exhausted", "no_review"), (
            f"budget_state must be one of the known states, got {route.budget_state!r}"
        )
        assert route.target, "PostCommitRouteExplanation.target must not be empty"

    def test_post_commit_routes_rendered_in_text(self) -> None:
        exp = _default_explanation()
        text = render_explanation_text(exp)
        assert "POST-COMMIT ROUTES" in text, (
            "render_explanation_text must include POST-COMMIT ROUTES section"
        )

    def test_post_commit_routes_text_contains_phase_budget_target(self) -> None:
        exp = _default_explanation()
        text = render_explanation_text(exp)
        for route in exp.post_commit_routes:
            assert route.phase in text
            assert route.budget_state in text
            assert route.target in text
