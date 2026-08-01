"""Public default-routing contract for the OpenCode smoke command."""

from __future__ import annotations

import inspect

from ralph.cli import main
from ralph.cli.commands.smoke import smoke_interactive_opencode_command


def test_opencode_smoke_default_regression_uses_current_minimax_provider() -> None:
    """S-2: the CLI and command seam use a provider published by ``opencode models``."""
    expected = "opencode/minimax/MiniMax-M3"

    cli_default = inspect.signature(main.smoke_interactive_opencode).parameters["agent"].default
    command_default = (
        inspect.signature(smoke_interactive_opencode_command).parameters["agent_name"].default
    )

    assert cli_default.default == expected
    assert command_default == expected
