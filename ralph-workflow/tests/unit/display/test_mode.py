from __future__ import annotations

from rich.console import Console

from ralph.display.context import make_display_context


def test_non_terminal_preserves_width() -> None:
    console = Console(force_terminal=False, width=120)
    ctx = make_display_context(console=console, env={})
    assert ctx.width == 120


def test_ci_env_does_not_affect_width() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={"CI": "1"})
    assert ctx.width == 120


def test_no_color_env_does_not_affect_width() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={"NO_COLOR": "1"})
    assert ctx.width == 120
