"""Tests for ralph/cli/commands/prompt_helper.py — post-artifact review loop."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.cli.commands.prompt_helper import run_prompt_helper

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from ralph.config.models import UnifiedConfig


class TestReviewLoopBehavior:
    """Tests for the post-artifact review loop state machine."""

    def _setup_base_mocks(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> MagicMock:
        """Set up common mocks for review loop tests."""
        mock_invoke_agent = MagicMock(return_value=iter([]))
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.invoke_agent", mock_invoke_agent
        )

        mock_bridge = MagicMock()
        mock_bridge.agent_endpoint_uri.return_value = "http://127.0.0.1:9999/mcp"
        mock_bridge.shutdown.return_value = None
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.start_mcp_server",
            lambda *args, **kwargs: mock_bridge,
        )
        return mock_invoke_agent

    def test_finish_action_writes_prompt_md(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Choosing Finish writes PROMPT.md from the artifact."""
        self._setup_base_mocks(monkeypatch)

        spec = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
        }
        # Always return the spec when artifact exists
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            lambda *args, **kwargs: spec,
        )

        # Prompt.ask returns "Finish" immediately
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.Prompt.ask",
            lambda *args, **kwargs: "Finish",
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        prompt_md_file = workspace_root / "PROMPT.md"
        assert prompt_md_file.exists(), "PROMPT.md should be written on Finish"

    def test_post_artifact_transition_with_realistic_agent_output(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Post-artifact transition works with realistic streaming agent output.

        Even when invoke_agent returns representative streaming output (not empty
        iterator), the review loop correctly transitions because the host, not
        the agent, owns the post-artifact state machine. The agent produces
        output and submits the artifact; the host checks for the artifact and
        presents Prompt.ask choices.
        """
        # Representative streaming output from the agent
        streaming_output = iter([
            "Let me help you define this product specification...",
            "I have a few questions to clarify your requirements.",
            "Based on your input, I'll structure the specification...",
            "SUBMITTING ARTIFACT: product_spec",
        ])

        mock_bridge = MagicMock()
        mock_bridge.agent_endpoint_uri.return_value = "http://127.0.0.1:9999/mcp"
        mock_bridge.shutdown.return_value = None
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.start_mcp_server",
            lambda *args, **kwargs: mock_bridge,
        )
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.invoke_agent",
            lambda *args, **kwargs: streaming_output,
        )

        spec = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
        }
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            lambda *args, **kwargs: spec,
        )

        # User chooses Finish at the review prompt
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.Prompt.ask",
            lambda *args, **kwargs: "Finish",
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        # PROMPT.md should be written because user chose Finish
        prompt_md_file = workspace_root / "PROMPT.md"
        assert prompt_md_file.exists(), (
            "PROMPT.md should be written when user chooses Finish, "
            "regardless of agent streaming output"
        )

    def test_continue_refining_reinvokes_agent_with_current_draft(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Choosing Continue refining re-invokes agent with current draft spec."""
        mock_invoke_agent = self._setup_base_mocks(monkeypatch)

        spec = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
        }
        # Always return the spec when artifact exists (artifact persists across calls)
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            lambda *args, **kwargs: spec,
        )

        # First Prompt.ask returns "Continue refining", second returns "Finish"
        prompt_calls = ["Continue refining", "Finish"]

        def mock_prompt_ask(*args: object, **kwargs: object) -> str:
            return prompt_calls.pop(0)

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.Prompt.ask",
            mock_prompt_ask,
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        # Agent should have been called twice (initial + continue refining)
        assert mock_invoke_agent.call_count == 2, (
            "Agent should be invoked twice: once initially and once for continue"
        )

        prompt_md_file = workspace_root / "PROMPT.md"
        assert prompt_md_file.exists(), "PROMPT.md should be written on Finish"

    def test_start_over_reinvokes_agent_without_draft(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Choosing Start over re-invokes agent with fresh intake (no draft)."""
        mock_invoke_agent = self._setup_base_mocks(monkeypatch)

        spec = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
        }
        # Track call count for read_product_spec_artifact
        read_call_count = [0]

        def mock_read_artifact(*args: object, **kwargs: object) -> dict[str, object] | None:
            read_call_count[0] += 1
            # Initial call returns spec, after start_over no new spec is produced
            return spec if read_call_count[0] <= 2 else None

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            mock_read_artifact,
        )

        # First Prompt.ask returns "Start over", second returns "Finish"
        prompt_calls = ["Start over", "Finish"]

        def mock_prompt_ask(*args: object, **kwargs: object) -> str:
            return prompt_calls.pop(0)

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.Prompt.ask",
            mock_prompt_ask,
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        # Agent should have been called twice (initial + start over)
        assert mock_invoke_agent.call_count == 2, (
            "Agent should be invoked twice: once initially and once for start over"
        )

        prompt_md_file = workspace_root / "PROMPT.md"
        assert prompt_md_file.exists(), "PROMPT.md should be written on Finish"

    def test_start_over_does_not_write_prompt_md_until_finish(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Choosing Start over does NOT write PROMPT.md until explicit Finish."""
        mock_invoke_agent = self._setup_base_mocks(monkeypatch)

        spec = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
        }
        # Track whether artifact has been "cleared" (simulates _clear_draft_artifact effect)
        artifact_cleared = [False]

        def mock_read_artifact(*args: object, **kwargs: object) -> dict[str, object] | None:
            # After start_over clears the draft, artifact should be gone
            if artifact_cleared[0]:
                return None
            return spec

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            mock_read_artifact,
        )

        # Patch _clear_draft_artifact to set our flag
        def mock_clear_draft(workspace_root: Path) -> None:
            artifact_cleared[0] = True

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper._clear_draft_artifact",
            mock_clear_draft,
        )

        # First Prompt.ask returns "Start over"
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.Prompt.ask",
            lambda *args, **kwargs: "Start over",
        )

        # Track whether _write_prompt_md was called
        write_prompt_md_called = [False]

        def mock_write_prompt_md(workspace_root: Path, spec: dict[str, object]) -> None:
            write_prompt_md_called[0] = True

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper._write_prompt_md",
            mock_write_prompt_md,
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        # _write_prompt_md should NOT have been called during the start over flow
        assert not write_prompt_md_called[0], (
            "_write_prompt_md should not be called until explicit Finish"
        )

        # Agent was invoked twice (initial + start over re-invoke)
        assert mock_invoke_agent.call_count == 2

    def test_update_section_reinvokes_agent_with_current_draft(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Choosing Update a section re-invokes agent with current draft spec."""
        mock_invoke_agent = self._setup_base_mocks(monkeypatch)

        spec = {
            "title": "Test Title",
            "scope": "Test scope",
            "goals": ["Goal 1"],
            "users": ["User 1"],
            "success_criteria": ["Criterion 1"],
        }
        # Always return the spec when artifact exists
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            lambda *args, **kwargs: spec,
        )

        # First Prompt.ask returns "Update a section", second returns "Finish"
        prompt_calls = ["Update a section", "Finish"]

        def mock_prompt_ask(*args: object, **kwargs: object) -> str:
            return prompt_calls.pop(0)

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.Prompt.ask",
            mock_prompt_ask,
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        # Agent should have been called twice (initial + update section)
        assert mock_invoke_agent.call_count == 2, (
            "Agent should be invoked twice: once initially and once for update"
        )

        prompt_md_file = workspace_root / "PROMPT.md"
        assert prompt_md_file.exists(), "PROMPT.md should be written on Finish"
