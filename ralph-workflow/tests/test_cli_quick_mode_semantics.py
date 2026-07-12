"""Unit tests for CLI."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from typer.testing import CliRunner as TyperCliRunner

from ralph.cli.main import (
    app,
)
from ralph.display.context import DisplayContext, make_display_context

if TYPE_CHECKING:
    from rich.console import Console

CliRunner = TyperCliRunner

RUN_PIPELINE_SUCCESS = 42
KEYBOARD_INTERRUPT_EXIT_CODE = 130
USAGE_ERROR_EXIT_CODE = 2
PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_POLICY_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"


def _make_display_context_for_console(console: Console) -> DisplayContext:
    """Create a DisplayContext for a given console."""
    return make_display_context(console=console, env={})


pytestmark = pytest.mark.timeout_seconds(5)


class TestQuickModeSemantics:
    """Tests for --quick/-Q flag behavior."""

    def test_quick_mode_forces_developer_iters_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
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
        runner.invoke(app, ["-Q", "--prompt", "do a task", "--dry-run"], catch_exceptions=False)

        cli_overrides = cast("dict[str, object]", captured.get("request").cli_overrides)
        general = cast("dict[str, object]", cli_overrides["general"])
        assert general["developer_iters"] == 1

    def test_quick_overrides_developer_iters_when_both_supplied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
        runner.invoke(
            app,
            ["-Q", "-D", "5", "--prompt", "do a task", "--dry-run"],
            catch_exceptions=False,
        )

        cli_overrides = cast("dict[str, object]", captured.get("request").cli_overrides)
        general = cast("dict[str, object]", cli_overrides["general"])
        assert general["developer_iters"] == 1

    def test_quick_mode_positional_text_is_passed_as_inline_prompt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
        runner.invoke(
            app,
            ["-Q", "do a quick change", "--dry-run"],
            catch_exceptions=False,
        )

        assert captured.get("request").inline_prompt == "do a quick change"

    def test_prompt_without_quick_raises_usage_error(
        self, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("ralph.cli.main._init_telemetry", lambda: None)
        result = cli_runner.invoke(app, ["--prompt", "some text"])
        assert result.exit_code == 2
        assert (
            "--prompt requires --quick/-Q" in result.stderr or "--prompt requires" in result.stdout
        )
