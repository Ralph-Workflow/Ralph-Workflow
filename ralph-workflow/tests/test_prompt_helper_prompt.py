"""Tests for ralph/cli/commands/prompt_helper_prompt.py — build_prompt_helper_prompt."""

from __future__ import annotations

from ralph.cli.commands.prompt_helper_prompt import build_prompt_helper_prompt


class TestBuildPromptHelperPrompt:
    """Tests for build_prompt_helper_prompt."""

    def test_prompt_asks_what_to_build(self) -> None:
        """Prompt contains opening question asking what user wants to build."""
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact"
        )
        assert "What do you want to build" in result

    def test_prompt_contains_follow_up_domains(self) -> None:
        """Prompt contains follow-up domains: users, goals, constraints, success, behavior."""
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact"
        )
        assert "users" in result.lower()
        assert "goals" in result.lower()
        assert "constraints" in result.lower()
        assert "success" in result.lower()
        assert "behavior" in result.lower()
        assert "ux/ui" in result.lower()

    def test_prompt_contains_review_loop_language(self) -> None:
        """Prompt contains review-loop language: present draft, ask if plan looks right."""
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact"
        )
        assert "review" in result.lower() or "draft" in result.lower()
        assert "polished" in result.lower() or "refined" in result.lower()

    def test_prompt_contains_ux_ui_capture_guidance(self) -> None:
        """Prompt contains explicit UX/UI capture guidance."""
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact"
        )
        assert (
            "ux/ui" in result.lower()
            or "usability" in result.lower()
            or "user-facing" in result.lower()
        )

    def test_prompt_contains_implementation_detail_avoidance(self) -> None:
        """Prompt contains explicit instruction to avoid implementation details."""
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact"
        )
        assert "implementation" in result.lower()
        assert "avoid" in result.lower() or "not" in result.lower()

    def test_prompt_injects_submit_artifact_tool_name(self) -> None:
        """Prompt contains the submit_artifact_tool_name value."""
        tool_name = "mcp__ralph__ralph_submit_artifact"
        result = build_prompt_helper_prompt(submit_artifact_tool_name=tool_name)
        assert tool_name in result

    def test_prompt_injects_different_tool_name(self) -> None:
        """Prompt correctly injects a different tool name when provided."""
        tool_name = "mcp__ralph__ralph_submit_artifact"
        result = build_prompt_helper_prompt(submit_artifact_tool_name=tool_name)
        assert tool_name in result

        different_tool_name = "custom__submit_artifact"
        result2 = build_prompt_helper_prompt(submit_artifact_tool_name=different_tool_name)
        assert different_tool_name in result2
        assert tool_name not in result2

    def test_prompt_contains_scale_adaptation_guidance(self) -> None:
        """Prompt contains scale-to-fit guidance for adapting structure to scope."""
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact"
        )
        assert "scale to fit" in result.lower()

    def test_prompt_contains_long_spec_handling_guidance(self) -> None:
        """Prompt contains guidance for managing long specifications."""
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact"
        )
        assert "chunk" in result.lower() or "regroup" in result.lower()

    def test_prompt_with_existing_prompt_md_asks_about_existing(self) -> None:
        """When prompt_md_exists=True, prompt asks user about existing PROMPT.md."""
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact",
            prompt_md_exists=True,
        )
        assert "PROMPT.md" in result
        assert "replace" in result.lower() or "refine" in result.lower()

    def test_prompt_without_existing_prompt_md_omits_existing_block(self) -> None:
        """When prompt_md_exists=False (default), prompt does not mention existing PROMPT.md."""
        result_default = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact"
        )
        result_explicit = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact",
            prompt_md_exists=False,
        )
        assert result_default == result_explicit
        assert "existing" not in result_default.lower() or "existing" not in result_explicit.lower()

    def test_prompt_contains_post_artifact_interactive_choices(self) -> None:
        """Prompt instructs agent to offer interactive choices after artifact submission."""
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact"
        )
        assert "continue refin" in result.lower() or "start over" in result.lower()

    def test_prompt_with_existing_prompt_md_includes_read_file_instruction(self) -> None:
        """When prompt_md_exists=True, prompt instructs agent to use read_file."""
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact",
            prompt_md_exists=True,
        )
        assert "read_file" in result

    def test_prompt_with_has_draft_includes_draft_context(self) -> None:
        """When has_draft=True with a current_draft, prompt includes draft content."""
        draft = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
        }
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact",
            has_draft=True,
            current_draft=draft,
        )
        assert "CURRENT DRAFT SPECIFICATION" in result
        assert "Test Title" in result

    def test_prompt_without_has_draft_omits_draft_context(self) -> None:
        """When has_draft=False, prompt does not include draft context."""
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact",
            has_draft=False,
        )
        assert "CURRENT DRAFT SPECIFICATION" not in result

    def test_prompt_does_not_reference_declare_complete(self) -> None:
        """Prompt does not reference declare_complete tool."""
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact"
        )
        assert "declare_complete" not in result.lower()

    def test_prompt_ends_session_on_user_choice_not_tool_call(self) -> None:
        """Prompt instructs agent to end session on user choice, not a tool call."""
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact"
        )
        # Should say user chooses Finish, not that agent calls a tool
        assert "FINISH" in result
        assert "respond with exactly the word" in result.lower()
