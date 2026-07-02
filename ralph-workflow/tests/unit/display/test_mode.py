from __future__ import annotations

import pytest
from rich.console import Console

from ralph.display.context import make_display_context


@pytest.mark.parametrize("width", [40, 60, 80, 100, 120, 200])
def test_any_width_returns_default_mode(width: int) -> None:
    """Single default-mode invariant: any width returns mode='default'."""
    console = Console(force_terminal=True, width=width)
    ctx = make_display_context(console=console, env={})
    assert ctx.mode == "default"


def test_non_terminal_default_mode() -> None:
    console = Console(force_terminal=False, width=120)
    ctx = make_display_context(console=console, env={})
    assert ctx.mode == "default"


def test_ci_env_does_not_affect_mode() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={"CI": "1"})
    assert ctx.mode == "default"


def test_no_color_env_does_not_affect_mode() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={"NO_COLOR": "1"})
    assert ctx.mode == "default"


def test_ralph_force_narrow_is_silently_ignored() -> None:
    """The historical RALPH_FORCE_NARROW env var is silently ignored."""
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={"RALPH_FORCE_NARROW": "1"})
    assert ctx.mode == "default"


def test_ralph_force_narrow_true_is_silently_ignored() -> None:
    """The historical RALPH_FORCE_NARROW env var is silently ignored for 'true' too."""
    console = Console(force_terminal=True, width=200)
    ctx = make_display_context(console=console, env={"RALPH_FORCE_NARROW": "true"})
    assert ctx.mode == "default"
