"""Tests for render_product_spec_as_prompt."""

from __future__ import annotations

from ralph.mcp.artifacts.product_spec import render_product_spec_as_prompt


class TestRenderProductSpecAsPrompt:
    """Tests for render_product_spec_as_prompt."""

    def test_render_produces_goal_heading(self) -> None:
        """Render output contains # Goal heading."""
        spec = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
        }
        result = render_product_spec_as_prompt(spec)
        assert "# Goal" in result

    def test_render_produces_context_heading(self) -> None:
        """Render output contains ## Context heading."""
        spec = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
        }
        result = render_product_spec_as_prompt(spec)
        assert "## Context" in result

    def test_render_produces_acceptance_criteria_heading(self) -> None:
        """Render output contains ## Acceptance criteria heading."""
        spec = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
        }
        result = render_product_spec_as_prompt(spec)
        assert "## Acceptance criteria" in result

    def test_render_places_scope_under_goal(self) -> None:
        """Scope content appears under # Goal heading."""
        spec = {
            "title": "Test Title",
            "scope": "The scope of this project is to build X",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
        }
        result = render_product_spec_as_prompt(spec)
        # Scope should be in the Goal section (after # Goal heading)
        goal_index = result.index("# Goal")
        context_index = result.index("## Context")
        goal_section = result[goal_index:context_index]
        assert "The scope of this project" in goal_section

    def test_render_places_success_criteria_under_acceptance_criteria(self) -> None:
        """Success criteria appears under ## Acceptance criteria heading."""
        spec = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Must work correctly", "Must be fast"],
        }
        result = render_product_spec_as_prompt(spec)
        acceptance_index = result.index("## Acceptance criteria")
        notes_index = result.index("## Notes") if "## Notes" in result else len(result)
        acceptance_section = result[acceptance_index:notes_index]
        assert "Must work correctly" in acceptance_section
        assert "Must be fast" in acceptance_section

    def test_render_places_goals_under_context(self) -> None:
        """Goals appear under ## Context heading."""
        spec = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Improve performance", "Reduce errors"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
        }
        result = render_product_spec_as_prompt(spec)
        context_index = result.index("## Context")
        acceptance_index = result.index("## Acceptance criteria")
        context_section = result[context_index:acceptance_index]
        assert "Improve performance" in context_section
        assert "Reduce errors" in context_section

    def test_render_omits_notes_when_scope_boundaries_and_open_questions_empty(
        self,
    ) -> None:
        """## Notes heading is omitted when both lists are empty."""
        spec = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
            "scope_boundaries": [],
            "open_questions": [],
        }
        result = render_product_spec_as_prompt(spec)
        assert "## Notes" not in result

    def test_render_includes_notes_when_scope_boundaries_non_empty(self) -> None:
        """## Notes heading appears when scope_boundaries is non-empty."""
        spec = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
            "scope_boundaries": ["Mobile is out of scope"],
            "open_questions": [],
        }
        result = render_product_spec_as_prompt(spec)
        assert "## Notes" in result
        assert "Mobile is out of scope" in result

    def test_render_includes_notes_when_open_questions_non_empty(self) -> None:
        """## Notes heading appears when open_questions is non-empty."""
        spec = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
            "scope_boundaries": [],
            "open_questions": ["Which API to use?"],
        }
        result = render_product_spec_as_prompt(spec)
        assert "## Notes" in result
        assert "Which API to use?" in result

    def test_render_omits_empty_optional_subgroups_in_context(self) -> None:
        """Empty optional sub-groups are omitted from ## Context."""
        spec = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
            "constraints": [],
            "product_behavior": [],
            "ux_ui_requirements": [],
        }
        result = render_product_spec_as_prompt(spec)
        context_index = result.index("## Context")
        acceptance_index = result.index("## Acceptance criteria")
        context_section = result[context_index:acceptance_index]
        assert "**Constraints:**" not in context_section
        assert "**Product behavior:**" not in context_section
        assert "**UX/UI requirements:**" not in context_section

    def test_render_includes_non_empty_optional_subgroups_in_context(self) -> None:
        """Non-empty optional sub-groups appear in ## Context."""
        spec = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
            "constraints": ["Budget limit"],
            "product_behavior": ["Should handle offline mode"],
            "ux_ui_requirements": ["Must be accessible"],
        }
        result = render_product_spec_as_prompt(spec)
        assert "**Constraints:**" in result
        assert "**Product behavior:**" in result
        assert "**UX/UI requirements:**" in result

    def test_render_handles_large_prd_style_spec_with_all_fields(self) -> None:
        """Large PRD-style spec with all 10 fields renders correctly."""
        spec = {
            "title": "Enterprise Platform Redesign",
            "scope": (
                "A comprehensive platform redesign spanning authentication, "
                "dashboard, reporting, and API endpoints."
            ),
            "goals": [
                "Modernize the authentication system",
                "Improve dashboard usability for power users",
                "Reduce report generation time by 50%",
                "Establish a scalable API foundation",
            ],
            "users": [
                "Enterprise administrators managing tenant configurations",
                "Power users performing daily tasks via the dashboard",
                "Business analysts generating recurring reports",
                "External API consumers integrating via REST endpoints",
            ],
            "constraints": [
                "Must support SAML 2.0 and OIDC authentication",
                "All UI components must be accessible to WCAG 2.1 AA",
                "API must maintain backward compatibility with v1",
            ],
            "success_criteria": [
                "90% of users complete core tasks in 2 clicks or fewer",
                "Dashboard loads in under 1.5 seconds on median hardware",
                "Report generation completes within 5 seconds for 95% of runs",
                "API responds within 200ms at the 95th percentile",
                "Zero downtime deployment with rolling update strategy",
            ],
            "product_behavior": [
                "[Auth] Single sign-on via SAML 2.0 and OIDC providers",
                "[Auth] Multi-factor authentication required for admin roles",
                "[Dashboard] Tasks appear in priority order by deadline and role",
                "[Dashboard] Notification banner persists without requiring dismissal",
                "[Reporting] Schedule recurring reports with configurable delivery",
                "[Reporting] Export to PDF, CSV, and Excel",
                "[API] Rate limits enforced at 1000 requests per minute per tenant",
            ],
            "ux_ui_requirements": [
                "Minimum touch target size of 44x44 pixels",
                "Color-blind safe palette with icon plus color indicators",
                "Keyboard navigable without mouse required",
                "Screen reader announces all interactive elements with ARIA labels",
                "Responsive layout adapts from 320px to 2560px viewport widths",
            ],
            "scope_boundaries": [
                "Mobile-native application",
                "Legacy SOAP API endpoints",
                "Third-party analytics integration in v1",
            ],
            "open_questions": [
                "Which identity provider should be primary for new tenants?",
                "Should report scheduling support timezone-aware delivery?",
                "What is the acceptable error budget for API rate limit violations?",
            ],
        }
        result = render_product_spec_as_prompt(spec)
        # Main headings
        assert "# Goal" in result
        assert "## Context" in result
        assert "## Acceptance criteria" in result
        assert "## Notes" in result
        # Context sub-group labels
        assert "**Goals:**" in result
        assert "**Users:**" in result
        assert "**Constraints:**" in result
        assert "**Product behavior:**" in result
        assert "**UX/UI requirements:**" in result
        # Title
        assert "Enterprise Platform Redesign" in result
        # Bracket-prefixed product behavior items
        assert "[Auth] Single sign-on" in result
        assert "[Dashboard] Tasks appear" in result
        # UX/UI requirement
        assert "Minimum touch target size" in result
        # Scope boundary
        assert "Mobile-native application" in result
        # Open questions (at least one)
        assert any(q in result for q in spec["open_questions"])
