from __future__ import annotations

import contextlib
import sys
from io import StringIO

import pytest
from loguru import logger as _loguru_logger
from rich.console import Console
from typer.testing import CliRunner as TyperCliRunner

from ralph.cli.main import app
from ralph.config.loader import ConfigTomlError
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.display.theme import RALPH_THEME

pytestmark = pytest.mark.timeout_seconds(5)

_ENVELOPE_TEXT = (
    "What failed: Ralph could not read "
    "/tmp/ralph-test/ralph-workflow.toml: unclosed bracket.\n"
    "Why it matters: settings in a malformed file are not safe to use.\n"
    "Fix: correct the TOML syntax in /tmp/ralph-test/ralph-workflow.toml, "
    "then run `ralph --check-config`."
)


@pytest.fixture(autouse=True)
def captured_stderr() -> None:

    def _dynamic_stderr_sink(message: object) -> None:
        sys.stderr.write(str(message))

    handler_id = _loguru_logger.add(_dynamic_stderr_sink, level="DEBUG", format="{message}")
    try:
        yield
    finally:
        with contextlib.suppress(ValueError):
            _loguru_logger.remove(handler_id)


def test_run_configuration_typo_emits_visible_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    from ralph.cli.commands import run as run_module

    stream = StringIO()
    context = make_display_context(
        console=Console(file=stream, force_terminal=False, theme=RALPH_THEME), env={}
    )

    def fake_load_config(*_args: object, **kwargs: object) -> UnifiedConfig:
        callback = kwargs["unknown_field_warning"]
        assert callable(callback)
        callback("What failed: unknown setting `gneral`")
        return UnifiedConfig()

    monkeypatch.setattr(run_module, "load_config", fake_load_config)

    result = run_module._load_configuration(
        __file__,
        {},
        False,
        display_context=context,
    )

    assert result != 1
    assert "unknown setting `gneral`" in stream.getvalue()


class TestConfigErrorEnvelope:
    def test_bootstrap_boundary_renders_envelope_without_traceback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "ralph.cli.main.bootstrap_global_configs",
            lambda *, display_context: (_ for _ in ()).throw(ConfigTomlError(_ENVELOPE_TEXT)),
        )
        monkeypatch.setenv("RALPH_DISABLE_TELEMETRY", "1")
        monkeypatch.setattr(
            "ralph.cli.main.configure_logging", lambda v, *, console_sink=None: None
        )

        result = TyperCliRunner().invoke(app, ["--check-config"])

        assert result.exit_code == 1
        combined = (result.output or "") + (result.stderr or "")
        assert "What failed:" in combined
        assert "Fix:" in combined
        assert "Traceback" not in combined

    def test_run_path_renders_envelope_without_traceback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "ralph.cli.main.bootstrap_global_configs", lambda *, display_context: None
        )
        monkeypatch.setenv("RALPH_DISABLE_TELEMETRY", "1")
        monkeypatch.setattr(
            "ralph.cli.main.configure_logging", lambda v, *, console_sink=None: None
        )
        monkeypatch.setattr(
            "ralph.cli.main.run_pipeline",
            lambda request, **kw: (_ for _ in ()).throw(ConfigTomlError(_ENVELOPE_TEXT)),
        )

        result = TyperCliRunner().invoke(app, ["--quick", "--dry-run"])

        assert result.exit_code == 1
        combined = (result.output or "") + (result.stderr or "")
        assert "What failed:" in combined
        assert "Fix:" in combined
        assert "Traceback" not in combined
