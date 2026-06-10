"""Tests for ralph/cli/commands/prompt_helper_prompt.py — build_prompt_helper_prompt."""

from __future__ import annotations

from ralph.cli.commands.prompt_helper_prompt import build_prompt_helper_prompt


class TestBuildPromptHelperPrompt:
    """Tests for build_prompt_helper_prompt."""

    def test_prompt_instructs_one_shot_submit_without_questions(self) -> None:
        """Prompt tells the agent to submit immediately and not to ask the user."""
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact"
        )
        lowered = result.lower()
        assert "immediately" in lowered
        assert "do not ask" in lowered

    def test_prompt_includes_user_idea_when_provided(self) -> None:
        """The host-supplied idea is embedded as a request block for the agent."""
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact",
            user_idea="A notes app with tags and search.",
        )
        assert "A notes app with tags and search." in result
        assert "USER REQUEST" in result

    def test_prompt_without_user_idea_omits_user_request_block(self) -> None:
        """Without an idea, no user-request block is included."""
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact"
        )
        assert "USER REQUEST" not in result

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

    def test_prompt_does_not_contain_conversational_intake_language(self) -> None:
        """Prompt must not tell the agent to converse with the user.

        The agent runs non-interactively; the only conversation is between the
        user and the host orchestrator, not the agent.
        """
        lowered = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact"
        ).lower()
        assert "ask the user" not in lowered
        assert "follow-up question" not in lowered
        assert "once the user is satisfied" not in lowered

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

    def test_prompt_with_existing_prompt_context_includes_context_not_menu(self) -> None:
        """Existing prompt context is injected as background, not an agent-owned menu."""
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact",
            existing_prompt_context="# Existing prompt\nBuild a notes app.",
        )
        assert "Build a notes app." in result
        assert "Replace it" not in result
        assert "Refine it" not in result

    def test_prompt_without_existing_prompt_context_omits_existing_block(self) -> None:
        """Without existing context, the helper prompt stays focused on fresh intake."""
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact"
        )
        assert "CURRENT PROMPT CONTEXT" not in result

    def test_prompt_does_not_contain_post_artifact_agent_side_menu(self) -> None:
        """Prompt does not instruct agent to present post-artifact choices.

        The post-artifact review menu is owned by the host (Ralph Workflow CLI)
        via Prompt.ask, not by the agent. The agent submits the artifact and
        the session continues; the host presents the review choices.
        """
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact"
        )
        # The agent should not be told to present post-artifact choices
        # (those are shown by the host via Prompt.ask)
        assert "Your product specification has been submitted" not in result
        assert "What would you like to do next" not in result

    def test_prompt_with_existing_prompt_context_does_not_delegate_file_reading_to_agent(
        self,
    ) -> None:
        """Existing prompt text is injected by the host, so the agent is not told to read files."""
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact",
            existing_prompt_context="# Existing prompt\nBuild a notes app.",
        )
        assert "read_file" not in result

    def test_prompt_with_existing_prompt_context_uses_safe_fence_for_embedded_backticks(
        self,
    ) -> None:
        """Existing prompt context stays inside the wrapper block when it contains fences."""
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact",
            existing_prompt_context="Before\n```py\nprint('x')\n```\nAfter",
        )
        assert "````md" in result
        assert "```py" in result
        assert "\n````\n\nYou are a product manager" in result

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

    def test_prompt_does_not_direct_agent_to_emit_finish_signal(self) -> None:
        """Prompt does not instruct agent to emit a FINISH signal after artifact.

        The post-artifact review loop is owned by the host (Ralph Workflow CLI),
        not by the agent. The agent submits the artifact and the session continues;
        the host presents the review choices via Prompt.ask.
        """
        result = build_prompt_helper_prompt(
            submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact"
        )
        # The FINISH contract no longer exists - host owns the review loop
        assert "FINISH" not in result
        assert "respond with exactly the word" not in result.lower()
