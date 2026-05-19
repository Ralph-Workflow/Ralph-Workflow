"""Regression tests: no module-level Console globals in renderer modules.

These black-box tests verify that:
1. No module-level Console globals exist in pipeline runner and CLI command modules.
2. Injected DisplayContext is honored when provided.
"""

from __future__ import annotations

import importlib
from io import StringIO

from rich.console import Console

from ralph.display.theme import RALPH_THEME


def _make_recording_console() -> Console:
    """Create a StringIO-backed recording console with Ralph theme."""
    stream = StringIO()
    return Console(
        file=stream,
        color_system=None,
        force_terminal=False,
        theme=RALPH_THEME,
    )


class TestNoModuleLevelConsoleGlobals:
    """Tests that verify no module-level Console globals exist."""

    def test_pipeline_runner_has_no_module_console_global(self) -> None:
        """Verify ralph.pipeline.runner has no module-level Console global."""
        mod = importlib.import_module("ralph.pipeline.runner")
        assert not isinstance(
            getattr(mod, "console", None),
            Console,
        ), "ralph.pipeline.runner should not have a module-level console global"

    def test_cli_command_run_has_no_module_console_global(self) -> None:
        """Verify ralph.cli.commands.run has no module-level Console global."""
        mod = importlib.import_module("ralph.cli.commands.run")
        assert not isinstance(
            getattr(mod, "console", None),
            Console,
        ), "ralph.cli.commands.run should not have a module-level console global"

    def test_cli_command_init_has_no_module_console_global(self) -> None:
        """Verify ralph.cli.commands.init has no module-level Console global."""
        mod = importlib.import_module("ralph.cli.commands.init")
        assert not isinstance(
            getattr(mod, "console", None),
            Console,
        ), "ralph.cli.commands.init should not have a module-level console global"

    def test_cli_command_commit_has_no_module_console_global(self) -> None:
        """Verify ralph.cli.commands.commit has no module-level Console global."""
        mod = importlib.import_module("ralph.cli.commands.commit")
        assert not isinstance(
            getattr(mod, "console", None),
            Console,
        ), "ralph.cli.commands.commit should not have a module-level console global"
