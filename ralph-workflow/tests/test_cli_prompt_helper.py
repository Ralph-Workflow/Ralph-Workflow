"""Tests for --prompt-helper CLI flag dispatch."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import typer.testing

from ralph.cli import main as main_module


@pytest.fixture
def cli_runner() -> typer.testing.CliRunner:
    """Return a typer CliRunner for testing."""
    return typer.testing.CliRunner()


class TestPromptHelperDispatch:
    """Tests for --prompt-helper flag dispatch."""

    @pytest.mark.timeout_seconds(3)
    @pytest.mark.subprocess_e2e
    def test_prompt_helper_flag_calls_run_prompt_helper(
        self,
        cli_runner: typer.testing.CliRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--prompt-helper dispatches to run_prompt_helper and calls typer.Exit."""
        mock_run_prompt_helper = MagicMock(return_value=None)
        # Patch at the import boundary in main module
        monkeypatch.setattr(main_module, "run_prompt_helper", mock_run_prompt_helper)

        # Also need to patch load_config to avoid actual config loading
        mock_config = MagicMock()
        mock_config.prompt_helper.agent = "prompt-helper-agent"
        monkeypatch.setattr(main_module, "load_config", lambda *args, **kwargs: mock_config)

        # Patch resolve_workspace_scope to return a mock
        mock_scope = MagicMock()
        mock_scope.root = Path("/fake/workspace")
        monkeypatch.setattr(main_module, "resolve_workspace_scope", lambda: mock_scope)

        result = cli_runner.invoke(
            main_module.app,
            ["--prompt-helper"],
            catch_exceptions=False,
        )

        # Should have called run_prompt_helper
        mock_run_prompt_helper.assert_called_once()
        # Should have raised typer.Exit (exit code 0)
        assert result.exit_code == 0

    @pytest.mark.timeout_seconds(3)
    def test_prompt_helper_flag_does_not_start_pipeline(
        self,
        cli_runner: typer.testing.CliRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--prompt-helper does NOT call run_pipeline."""
        mock_run_prompt_helper = MagicMock(return_value=None)
        monkeypatch.setattr(main_module, "run_prompt_helper", mock_run_prompt_helper)

        mock_config = MagicMock()
        mock_config.prompt_helper.agent = "prompt-helper-agent"
        monkeypatch.setattr(main_module, "load_config", lambda *args, **kwargs: mock_config)

        mock_scope = MagicMock()
        mock_scope.root = Path("/fake/workspace")
        monkeypatch.setattr(main_module, "resolve_workspace_scope", lambda: mock_scope)

        # Patch invoke_pipeline to track if it's called
        mock_invoke_pipeline = MagicMock(return_value=0)
        monkeypatch.setattr(main_module, "invoke_pipeline", mock_invoke_pipeline)

        result = cli_runner.invoke(
            main_module.app,
            ["--prompt-helper"],
            catch_exceptions=False,
        )

        # invoke_pipeline should NOT have been called
        mock_invoke_pipeline.assert_not_called()
        assert result.exit_code == 0

    @pytest.mark.timeout_seconds(3)
    def test_without_prompt_helper_flag_does_not_call_run_prompt_helper(
        self,
        cli_runner: typer.testing.CliRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without --prompt-helper, run_prompt_helper is NOT called."""
        mock_run_prompt_helper = MagicMock(return_value=None)
        monkeypatch.setattr(main_module, "run_prompt_helper", mock_run_prompt_helper)

        # We don't need to fully mock pipeline since we just want to verify
        # run_prompt_helper is NOT called when the flag is absent
        result = cli_runner.invoke(
            main_module.app,
            ["--help"],
            catch_exceptions=False,
        )

        # --help should work without calling run_prompt_helper
        mock_run_prompt_helper.assert_not_called()
        assert result.exit_code == 0

    @pytest.mark.timeout_seconds(3)
    def test_prompt_helper_passes_workspace_scope_to_load_config(
        self,
        cli_runner: typer.testing.CliRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--prompt-helper passes workspace_scope to load_config."""
        monkeypatch.setattr(main_module, "run_prompt_helper", MagicMock(return_value=None))

        captured_kwargs: dict[str, object] = {}

        def tracking_load_config(*args: object, **kwargs: object) -> object:
            captured_kwargs.update(kwargs)
            mock_config = MagicMock()
            mock_config.prompt_helper.agent = "prompt-helper-agent"
            return mock_config

        monkeypatch.setattr(main_module, "load_config", tracking_load_config)

        mock_scope = MagicMock()
        mock_scope.root = Path("/fake/workspace")
        monkeypatch.setattr(main_module, "resolve_workspace_scope", lambda: mock_scope)

        result = cli_runner.invoke(
            main_module.app,
            ["--prompt-helper"],
            catch_exceptions=False,
        )

        assert result.exit_code == 0
        assert "workspace_scope" in captured_kwargs, (
            "load_config must be called with workspace_scope= kwarg"
        )
        assert captured_kwargs["workspace_scope"] is mock_scope
