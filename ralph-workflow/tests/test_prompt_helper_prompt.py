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

    def test_prompt_contains_declare_complete(self) -> None:
        """Prompt contains declare_complete instruction."""
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact"
        )
        assert "declare_complete" in result

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
