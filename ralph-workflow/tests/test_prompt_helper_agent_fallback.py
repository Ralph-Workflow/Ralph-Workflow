"""Tests for agent fallback behaviour in run_prompt_helper."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from ralph.cli.commands.prompt_helper import run_prompt_helper
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.config.prompt_helper_config import PromptHelperConfig

if TYPE_CHECKING:
    from pathlib import Path


class TestAgentFallback:
    """Tests for agent fallback behaviour in run_prompt_helper."""

    def _stub_mcp_and_invoke(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.invoke_agent",
            MagicMock(return_value=iter([])),
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

    def test_falls_back_to_first_configured_agent(
        self,
        workspace_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When configured agent is missing, falls back to first agent in config."""
        config = UnifiedConfig(
            prompt_helper=PromptHelperConfig(agent="nonexistent-agent"),
            agents={
                "fallback-agent": AgentConfig(
                    cmd="claude",
                    transport=AgentTransport.CLAUDE_INTERACTIVE,
                )
            },
        )
        self._stub_mcp_and_invoke(monkeypatch)
        # Should not raise — fallback-agent is used instead
        run_prompt_helper(config, workspace_root)

    def test_raises_when_no_fallback_agent_available(
        self,
        workspace_root: Path,
    ) -> None:
        """When configured agent is missing and no agents are configured, raises RuntimeError."""
        config = UnifiedConfig(
            prompt_helper=PromptHelperConfig(agent="nonexistent-agent"),
            agents={},
        )
        with pytest.raises(RuntimeError, match="no fallback agent is available"):
            run_prompt_helper(config, workspace_root)
