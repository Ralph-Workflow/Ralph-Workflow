"""Tests for ralph/cli/commands/prompt_helper.py — run_prompt_helper."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from ralph.cli.commands.prompt_helper import run_prompt_helper
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, UnifiedConfig

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    """Return a temporary workspace root."""
    return tmp_path


@pytest.fixture
def config_with_helper_agent() -> UnifiedConfig:
    """Return a UnifiedConfig with prompt-helper-agent in the agents dict."""
    return UnifiedConfig(
        agents={
            "prompt-helper-agent": AgentConfig(
                cmd="claude",
                transport=AgentTransport.CLAUDE_INTERACTIVE,
            )
        }
    )


class TestRunPromptHelper:
    """Tests for run_prompt_helper."""

    def test_creates_prompt_file(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """run_prompt_helper creates prompt file at .agent/tmp/prompt_helper_prompt.md."""
        # Stub invoke_agent and start_mcp_server at the import boundary
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

        # Patch read_product_spec_artifact to return None (no artifact)
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
        # The tool name should appear in the prompt
        assert "ralph_submit_artifact" in content

    def test_writes_prompt_md_when_artifact_exists(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When product_spec artifact exists, PROMPT.md is written with render output."""
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

        # Provide a valid product_spec artifact
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

        run_prompt_helper(config_with_helper_agent, workspace_root)

        prompt_md_file = workspace_root / "PROMPT.md"
        assert prompt_md_file.exists()
        content = prompt_md_file.read_text(encoding="utf-8")
        # Should contain the canonical PROMPT.md structure
        assert "# Goal" in content
        assert "Test Title" in content

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

        # Return None (no artifact)
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            lambda *args, **kwargs: None,
        )

        run_prompt_helper(config_with_helper_agent, workspace_root)

        prompt_md_file = workspace_root / "PROMPT.md"
        assert not prompt_md_file.exists()
