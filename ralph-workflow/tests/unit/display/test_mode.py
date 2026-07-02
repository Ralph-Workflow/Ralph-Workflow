from __future__ import annotations

import pytest
from rich.console import Console

from ralph.display.context import make_display_context


@pytest.mark.parametrize("width", [40, 60, 80, 100, 120, 200])
def test_any_width_preserves_width(width: int) -> None:
    """Single default-mode invariant: input width is preserved on the context."""
    console = Console(force_terminal=True, width=width)
    ctx = make_display_context(console=console, env={})
    assert ctx.width == width


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
