"""CliRunner helper for test_cli_parse_counter_overrides.py."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from typer.testing import CliRunner as TyperCliRunner

from ralph.cli.main import app
from tests.test_cli_parse_counter_overrides_cli_result import CliResult

if TYPE_CHECKING:
    from collections.abc import ContextManager, Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class CliRunner:
    def __init__(self) -> None:
        self._cwd = PROJECT_ROOT
        self._runner = TyperCliRunner()

    def invoke(self, _app: object, args: list[str]) -> CliResult:
        with self._pushd(self._cwd):
            result = self._runner.invoke(app, args, catch_exceptions=False)
        stderr = getattr(result, "stderr", "")
        return CliResult(result.exit_code, result.stdout, stderr)

    @contextmanager
    def _pushd(self, path: Path) -> Iterator[None]:
        original_cwd = Path.cwd()
        try:
            os.chdir(path)
            yield
        finally:
            os.chdir(original_cwd)

    @contextmanager
    def isolated_filesystem(self, temp_dir: Path) -> ContextManager[Path]:
        temp_dir.mkdir(parents=True, exist_ok=True)
        with self._runner.isolated_filesystem(temp_dir):
            yield temp_dir
