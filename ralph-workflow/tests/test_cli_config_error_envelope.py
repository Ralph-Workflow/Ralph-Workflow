"""Regression tests for traceback-free config-error envelopes.

A malformed user-global TOML must surface Ralph's existing what/why/fix
envelope on every CLI command (bootstrap boundary + run path), with
exit code 1 and no raw Python traceback. Pin the contract here so a
future change to ``main()`` or ``_run_pipeline`` cannot regress to
the pre-fix behaviour of printing a rich traceback to the terminal.
"""

from __future__ import annotations

import contextlib
import sys

import pytest
from loguru import logger as _loguru_logger
from typer.testing import CliRunner as TyperCliRunner

from ralph.cli.main import app
from ralph.config.loader import ConfigTomlError

pytestmark = pytest.mark.timeout_seconds(5)

_ENVELOPE_TEXT = (
    "What failed: Ralph could not read "
    "/tmp/ralph-test/ralph-workflow.toml: unclosed bracket.\n"
    "Why it matters: settings in a malformed file are not safe to use.\n"
    "Fix: correct the TOML syntax in /tmp/ralph-test/ralph-workflow.toml, "
    "then run `ralph --check-config`."
)


@pytest.fixture
def captured_stderr() -> None:
    """Pin a loguru sink on the dynamically-resolved ``sys.stderr``.

    loguru's default sink captures ``sys.stderr`` at import time and
    therefore bypasses Typer's ``CliRunner`` isolation. Without this
    fixture the run-path test cannot observe a traceback in
    ``result.output`` and would silently false-pass on the unfixed
    code path. The fixture installs a callable sink that resolves
    ``sys.stderr`` at call time (so click's wrapped stream is used)
    and removes it after the test.
    """

    def _dynamic_stderr_sink(message: object) -> None:
        sys.stderr.write(str(message))

    handler_id = _loguru_logger.add(_dynamic_stderr_sink, level="DEBUG", format="{message}")
    try:
        yield
    finally:
        with contextlib.suppress(ValueError):
            _loguru_logger.remove(handler_id)


class TestConfigErrorEnvelope:
    """Config errors must render the envelope with no Python traceback."""

    def test_bootstrap_boundary_renders_envelope_without_traceback(
        self, monkeypatch: pytest.MonkeyPatch, captured_stderr: None
    ) -> None:
        """A ``ConfigTomlError`` from bootstrap exits 1 with the envelope, no traceback."""
        monkeypatch.setattr(
            "ralph.cli.main.bootstrap_global_configs",
            lambda *, display_context: (_ for _ in ()).throw(ConfigTomlError(_ENVELOPE_TEXT)),
        )
        monkeypatch.setattr("ralph.cli.main._init_telemetry", lambda: None)
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
        self, monkeypatch: pytest.MonkeyPatch, captured_stderr: None
    ) -> None:
        """A ``ConfigTomlError`` raised inside ``run_pipeline`` returns 1 with the envelope, no traceback."""
        monkeypatch.setattr(
            "ralph.cli.main.bootstrap_global_configs", lambda *, display_context: None
        )
        monkeypatch.setattr("ralph.cli.main._init_telemetry", lambda: None)
        monkeypatch.setattr(
            "ralph.cli.main.configure_logging", lambda v, *, console_sink=None: None
        )
        monkeypatch.setattr(
            "ralph.cli.main.run_pipeline",
            lambda request, **kw: (_ for _ in ()).throw(ConfigTomlError(_ENVELOPE_TEXT)),
        )

        result = TyperCliRunner().invoke(app, ["--quick", "--prompt", "task", "--dry-run"])

        assert result.exit_code == 1
        combined = (result.output or "") + (result.stderr or "")
        assert "What failed:" in combined
        assert "Fix:" in combined
        assert "Traceback" not in combined
