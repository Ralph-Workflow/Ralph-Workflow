"""Unit tests for CLI."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from typer.testing import CliRunner as TyperCliRunner

from ralph.cli.main import (
    CLIOverrideInput,
    app,
    build_cli_overrides,
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


class TestIterationCounterFlags:
    def test_developer_iters_flag_sets_config_override(self) -> None:
        overrides = cast(
            "dict[str, object]",
            build_cli_overrides(CLIOverrideInput(developer_iters=3)),
        )
        general = cast("dict[str, object]", overrides["general"])
        assert general["developer_iters"] == 3

    def test_counter_flag_passes_overrides_to_run_pipeline(
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
            ["--counter", "iteration=2", "--counter", "reviewer_pass=1", "--dry-run"],
            catch_exceptions=False,
        )

        assert captured.get("request").counter_overrides == {"iteration": 2, "reviewer_pass": 1}
