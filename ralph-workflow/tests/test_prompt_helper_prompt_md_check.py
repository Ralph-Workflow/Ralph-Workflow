"""Tests for PROMPT.md existence detection in run_prompt_helper."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.cli.commands.prompt_helper import run_prompt_helper

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from ralph.config.models import UnifiedConfig


class TestPromptMdCheck:
    """Tests for PROMPT.md existence detection in run_prompt_helper."""

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

    def test_prompt_file_differs_when_prompt_md_exists(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Prompt file content differs when PROMPT.md exists vs when it does not."""
        self._stub_mcp_and_invoke(monkeypatch)

        # Run without existing PROMPT.md
        run_prompt_helper(config_with_helper_agent, workspace_root)
        prompt_file = workspace_root / ".agent" / "tmp" / "prompt_helper_prompt.md"
        content_without = prompt_file.read_text(encoding="utf-8")

        # Place a PROMPT.md and run again
        (workspace_root / "PROMPT.md").write_text("existing", encoding="utf-8")
        run_prompt_helper(config_with_helper_agent, workspace_root)
        content_with = prompt_file.read_text(encoding="utf-8")

        assert content_with != content_without

    def test_prompt_file_contains_prompt_md_language_when_exists(
        self,
        workspace_root: Path,
        config_with_helper_agent: UnifiedConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When PROMPT.md exists, the prompt file contains PROMPT.md-related language."""
        self._stub_mcp_and_invoke(monkeypatch)

        (workspace_root / "PROMPT.md").write_text("existing", encoding="utf-8")
        run_prompt_helper(config_with_helper_agent, workspace_root)

        prompt_file = workspace_root / ".agent" / "tmp" / "prompt_helper_prompt.md"
        content = prompt_file.read_text(encoding="utf-8")
        assert "PROMPT.md" in content
        assert "replace" in content.lower() or "refine" in content.lower()
