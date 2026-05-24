"""Tests for ralph/cli/_prompt_helper_entry.py — ralph-prompt entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest


class TestRalphPromptEntry:
    """Tests for the ralph-prompt entrypoint module."""

    def test_main_calls_run_prompt_helper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """main() calls run_prompt_helper with config and workspace_root."""
        mock_run = MagicMock()
        fake_module = ModuleType("ralph.cli.commands.prompt_helper")
        cast("Any", fake_module).run_prompt_helper = mock_run
        monkeypatch.setitem(sys.modules, "ralph.cli.commands.prompt_helper", fake_module)
        mock_scope = MagicMock()
        mock_scope.root = Path("/tmp/fake-workspace")
        mock_cfg = MagicMock()

        with (
            patch("ralph.config.bootstrap.ensure_global_config"),
            patch("ralph.config.bootstrap.ensure_global_mcp_config"),
            patch("ralph.config.bootstrap.ensure_global_policy_configs"),
            patch(
                "ralph.workspace.scope.resolve_workspace_scope",
                return_value=mock_scope,
            ),
            patch("ralph.config.loader.load_config", return_value=mock_cfg),
        ):
            from ralph.cli._prompt_helper_entry import main

            main()

        mock_run.assert_called_once_with(mock_cfg, mock_scope.root)

    def test_main_exits_with_code_1_on_config_error(self) -> None:
        """main() exits with code 1 when config bootstrap raises an exception."""
        with (
            patch(
                "ralph.config.bootstrap.ensure_global_config",
                side_effect=RuntimeError("config failed"),
            ),
            patch("builtins.print"),
        ):
            from ralph.cli._prompt_helper_entry import main

            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 1

    def test_pyproject_declares_ralph_prompt_script(self) -> None:
        """pyproject.toml declares ralph-prompt pointing to _prompt_helper_entry:main."""
        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        content = pyproject.read_text(encoding="utf-8")
        assert 'ralph-prompt = "ralph.cli._prompt_helper_entry:main"' in content
