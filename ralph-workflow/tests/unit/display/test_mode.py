from __future__ import annotations

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.mode import NARROW_THRESHOLD


def test_wide_console_returns_wide() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={})

    assert ctx.mode == "wide"


def test_narrow_console_returns_compact() -> None:
    console = Console(force_terminal=True, width=40)
    ctx = make_display_context(console=console, env={})

    assert ctx.mode == "compact"


def test_threshold_boundary_returns_medium() -> None:
    # width == NARROW_THRESHOLD (60) is not < 60, so falls into medium tier
    console = Console(force_terminal=True, width=NARROW_THRESHOLD)
    ctx = make_display_context(console=console, env={})

    assert ctx.mode == "medium"


def test_non_terminal_wide_returns_wide() -> None:
    console = Console(force_terminal=False, width=120)
    ctx = make_display_context(console=console, env={})

    assert ctx.mode == "wide"


def test_ci_env_does_not_affect_mode() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={"CI": "1"})

    assert ctx.mode == "wide"


def test_no_color_env_does_not_affect_mode() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={"NO_COLOR": "1"})

    assert ctx.mode == "wide"


def test_ralph_force_narrow_returns_compact() -> None:
    console = Console(force_terminal=True, width=120)
    ctx = make_display_context(console=console, env={"RALPH_FORCE_NARROW": "1"})

    assert ctx.mode == "compact"


def test_ralph_force_narrow_true_returns_compact() -> None:
    console = Console(force_terminal=True, width=200)
    ctx = make_display_context(console=console, env={"RALPH_FORCE_NARROW": "true"})

    assert ctx.mode == "compact"
