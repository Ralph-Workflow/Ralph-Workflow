"""Unit tests for CLI."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from typer.testing import CliRunner as TyperCliRunner

from ralph.cli.main import (
    app,
)
from ralph.display.context import DisplayContext, make_display_context

if TYPE_CHECKING:
    import pytest
    from rich.console import Console

RUN_PIPELINE_SUCCESS = 42
KEYBOARD_INTERRUPT_EXIT_CODE = 130
USAGE_ERROR_EXIT_CODE = 2
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_POLICY_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


def _make_display_context_for_console(console: Console) -> DisplayContext:
    """Create a DisplayContext for a given console."""
    return make_display_context(console=console, env={})


class TestAdditionalShortcutAliases:
    """Tests for added short aliases on common control flags."""

    def test_short_resume_alias_sets_resume(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}
        monkeypatch.setattr(
            "ralph.cli.main.run_pipeline",
            lambda request, **kw: captured.update({"request": request, **kw}) or 0,
        )
        monkeypatch.setattr(
            "ralph.cli.main.bootstrap_global_configs", lambda *, display_context: None
        )
        monkeypatch.setattr("ralph.cli.main.configure_logging", lambda v, *, console_sink=None: None)
        monkeypatch.setattr("ralph.cli.main._init_telemetry", lambda: None)

        runner = TyperCliRunner()
        runner.invoke(app, ["-r", "--dry-run"], catch_exceptions=False)

        assert captured.get("request").resume is True

    def test_short_check_config_alias_runs_check_config(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "ralph.cli.main.bootstrap_global_configs", lambda *, display_context: None
        )
        monkeypatch.setattr("ralph.cli.main.configure_logging", lambda v, *, console_sink=None: None)
        monkeypatch.setattr("ralph.cli.main._init_telemetry", lambda: None)
        monkeypatch.setattr(
            "ralph.cli.main.handle_check_config",
            lambda config, cli_overrides, check_config, *, console: 0 if check_config else None,
        )

        runner = TyperCliRunner()
        result = runner.invoke(app, ["-C"], catch_exceptions=False)

        assert result.exit_code == 0
