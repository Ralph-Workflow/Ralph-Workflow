"""Unit tests for CLI."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner as TyperCliRunner

from ralph.cli.main import (
    LONG_DEVELOPER_ITERS,
    app,
)
from tests._support.typed_accessors import must_mapping

CliRunner = TyperCliRunner

USAGE_ERROR_EXIT_CODE = 2

pytestmark = pytest.mark.timeout_seconds(5)


def _stub_pipeline(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "ralph.cli.main.run_pipeline",
        lambda request, **kw: captured.update({"request": request, **kw}) or 0,
    )
    monkeypatch.setattr("ralph.cli.main.bootstrap_global_configs", lambda *, display_context: None)
    monkeypatch.setattr("ralph.cli.main.configure_logging", lambda v, *, console_sink=None: None)
    monkeypatch.setattr("ralph.cli.main._init_telemetry", lambda: None)
    return captured


def _captured_developer_iters(captured: dict[str, object]) -> object:
    cli_overrides = must_mapping(captured["request"].cli_overrides)
    general = must_mapping(cli_overrides["general"])
    return general["developer_iters"]


class TestLongModeSemantics:
    """Tests for --long/-L flag behavior."""

    def test_long_mode_forces_developer_iters_5(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = _stub_pipeline(monkeypatch)

        TyperCliRunner().invoke(app, ["-L", "--dry-run"], catch_exceptions=False)

        assert _captured_developer_iters(captured) == LONG_DEVELOPER_ITERS

    def test_long_overrides_developer_iters_when_both_supplied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _stub_pipeline(monkeypatch)

        TyperCliRunner().invoke(app, ["-L", "-D", "3", "--dry-run"], catch_exceptions=False)

        assert _captured_developer_iters(captured) == LONG_DEVELOPER_ITERS

    def test_long_flag_has_long_form(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = _stub_pipeline(monkeypatch)

        TyperCliRunner().invoke(app, ["--long", "--dry-run"], catch_exceptions=False)

        assert _captured_developer_iters(captured) == LONG_DEVELOPER_ITERS

    def test_quick_and_long_together_raise_usage_error(
        self, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("ralph.cli.main._init_telemetry", lambda: None)
        result = cli_runner.invoke(app, ["-Q", "-L", "--prompt", "task"])
        assert result.exit_code == USAGE_ERROR_EXIT_CODE
        assert "--quick/-Q and --long/-L cannot be used together" in (
            result.stderr or result.stdout
        )

    def test_long_and_thorough_together_raise_usage_error(
        self, cli_runner: CliRunner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("ralph.cli.main._init_telemetry", lambda: None)
        result = cli_runner.invoke(app, ["-L", "-T"])
        assert result.exit_code == USAGE_ERROR_EXIT_CODE
        assert "--long/-L and --thorough/-T cannot be used together" in (
            result.stderr or result.stdout
        )
