"""Tests for ralph/cli/commands/prompt_helper.py — run_prompt_helper."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.cli.commands.prompt_helper import ReviewAction, run_prompt_helper

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from ralph.config.models import UnifiedConfig


class TestRunPromptHelper:
    """Tests for run_prompt_helper."""

    def test_creates_prompt_file(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """run_prompt_helper creates prompt file at .agent/tmp/prompt_helper_prompt.md."""
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

        # No artifact - session ends silently
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            lambda *args, **kwargs: None,
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        prompt_file = workspace_root / ".agent" / "tmp" / "prompt_helper_prompt.md"
        assert prompt_file.exists()

    def test_prompt_file_contains_tool_name(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Prompt file contains the submit_artifact_tool_name."""
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

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            lambda *args, **kwargs: None,
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        prompt_file = workspace_root / ".agent" / "tmp" / "prompt_helper_prompt.md"
        content = prompt_file.read_text(encoding="utf-8")
        assert "ralph_submit_artifact" in content

    def test_does_not_write_prompt_md_when_no_artifact(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When no product_spec artifact, PROMPT.md is NOT written."""
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

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            lambda *args, **kwargs: None,
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        prompt_md_file = workspace_root / "PROMPT.md"
        assert not prompt_md_file.exists()

    def test_writes_prompt_md_when_artifact_exists(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When artifact exists, _handle_artifact_exists is invoked."""
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

        # Mock _handle_artifact_exists to capture the call
        handle_called = {"called": False, "spec": None}

        def mock_handle(
            workspace_root: Path,
            agent_config: object,
            options: object,
            prompt_md_exists: bool,
            submit_artifact_tool_name: str,
            spec: dict[str, object],
            session_id: str | None,
        ) -> None:
            del (
                workspace_root,
                agent_config,
                options,
                prompt_md_exists,
                submit_artifact_tool_name,
                session_id,
            )
            handle_called["called"] = True
            handle_called["spec"] = spec

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper._handle_artifact_exists",
            mock_handle,
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        assert handle_called["called"], "_handle_artifact_exists was not called"
        assert handle_called["spec"] == spec

    def test_no_artifact_means_no_review_loop(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When no artifact exists, _handle_artifact_exists is NOT invoked."""
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

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            lambda *args, **kwargs: None,
        )

        # Mock _handle_artifact_exists to track if it's called
        handle_called = {"called": False}

        def mock_handle(*args: object, **kwargs: object) -> None:
            handle_called["called"] = True

        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper._handle_artifact_exists",
            mock_handle,
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        assert not handle_called["called"], (
            "_handle_artifact_exists was called but should not have been"
        )

    def test_review_action_enum_values(self) -> None:
        """ReviewAction enum has expected values."""
        assert ReviewAction.CONTINUE.value == "continue"
        assert ReviewAction.UPDATE_SECTION.value == "update"
        assert ReviewAction.START_OVER.value == "start_over"
        assert ReviewAction.FINISH.value == "finish"
