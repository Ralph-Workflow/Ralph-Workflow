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
