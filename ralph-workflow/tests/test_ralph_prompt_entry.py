"""Tests for ralph/cli/_prompt_helper_entry.py  ralph-prompt entrypoint."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

_PROMPT_HELPER_ENTRY_PATH = (
    Path(__file__).parent.parent / "ralph" / "cli" / "_prompt_helper_entry.py"
)


def _load_prompt_helper_entry_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "prompt_helper_entry_test_module",
        _PROMPT_HELPER_ENTRY_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestRalphPromptEntry:
    """Tests for the ralph-prompt entrypoint module."""

    def test_main_calls_run_prompt_helper(self) -> None:
        """main() calls run_prompt_helper with config and workspace_root."""
        mock_run = MagicMock()
        mock_scope = MagicMock()
        mock_scope.root = Path("/tmp/fake-workspace")
        mock_cfg = MagicMock()
        fake_prompt_helper = ModuleType("ralph.cli.commands.prompt_helper")
        fake_prompt_helper.__dict__["run_prompt_helper"] = mock_run

        with (
            patch.dict(sys.modules, {"ralph.cli.commands.prompt_helper": fake_prompt_helper}),
            patch("ralph.config.bootstrap.ensure_global_config"),
            patch("ralph.config.bootstrap.ensure_global_mcp_config"),
            patch("ralph.config.bootstrap.ensure_global_policy_configs"),
            patch(
                "ralph.workspace.scope.resolve_workspace_scope",
                return_value=mock_scope,
            ),
            patch("ralph.config.loader.load_config", return_value=mock_cfg),
        ):
            module = _load_prompt_helper_entry_module()
            module.main()

        mock_run.assert_called_once_with(mock_cfg, mock_scope.root)

    def test_main_exits_with_code_1_on_config_error(self) -> None:
        """main() exits with code 1 when config bootstrap raises an exception."""
        fake_prompt_helper = ModuleType("ralph.cli.commands.prompt_helper")
        fake_prompt_helper.__dict__["run_prompt_helper"] = MagicMock()

        with (
            patch.dict(sys.modules, {"ralph.cli.commands.prompt_helper": fake_prompt_helper}),
            patch(
                "ralph.config.bootstrap.ensure_global_config",
                side_effect=RuntimeError("config failed"),
            ),
            patch("builtins.print"),
        ):
            module = _load_prompt_helper_entry_module()

            with pytest.raises(SystemExit) as exc_info:
                module.main()

        assert exc_info.value.code == 1

    def test_pyproject_declares_ralph_prompt_script(self) -> None:
        """pyproject.toml declares ralph-prompt pointing to _prompt_helper_entry:main."""
        pyproject = Path(__file__).parent.parent / "pyproject.toml"
        content = pyproject.read_text(encoding="utf-8")
        assert 'ralph-prompt = "ralph.cli._prompt_helper_entry:main"' in content
