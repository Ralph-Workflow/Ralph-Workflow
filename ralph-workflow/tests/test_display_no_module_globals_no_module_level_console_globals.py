from __future__ import annotations

import importlib
from io import StringIO

from rich.console import Console

from ralph.display.theme import RALPH_THEME


def _make_recording_console() -> Console:
    stream = StringIO()
    return Console(
        file=stream,
        color_system=None,
        force_terminal=False,
        theme=RALPH_THEME,
    )


class TestNoModuleLevelConsoleGlobals:
    def test_pipeline_runner_has_no_module_console_global(self) -> None:
        mod = importlib.import_module("ralph.pipeline.runner")
        assert not isinstance(
            getattr(mod, "console", None),
            Console,
        ), "ralph.pipeline.runner should not have a module-level console global"

    def test_cli_command_run_has_no_module_console_global(self) -> None:
        mod = importlib.import_module("ralph.cli.commands.run")
        assert not isinstance(
            getattr(mod, "console", None),
            Console,
        ), "ralph.cli.commands.run should not have a module-level console global"

    def test_cli_command_init_has_no_module_console_global(self) -> None:
        mod = importlib.import_module("ralph.cli.commands.init")
        assert not isinstance(
            getattr(mod, "console", None),
            Console,
        ), "ralph.cli.commands.init should not have a module-level console global"

    def test_cli_command_commit_has_no_module_console_global(self) -> None:
        mod = importlib.import_module("ralph.cli.commands.commit")
        assert not isinstance(
            getattr(mod, "console", None),
            Console,
        ), "ralph.cli.commands.commit should not have a module-level console global"
