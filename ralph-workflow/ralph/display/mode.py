"""Terminal mode detection for Ralph's transcript output."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from rich.console import Console

NARROW_THRESHOLD: int = 60

_RALPH_FORCE_NARROW_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def detect_mode(
    console: Console,
    env: dict[str, str],
) -> Literal["compact", "wide"]:
    """Detect display mode from terminal width and environment.

    Returns 'compact' when the terminal is narrower than NARROW_THRESHOLD or
    RALPH_FORCE_NARROW is set to a truthy value. Returns 'wide' otherwise.

    Args:
        console: Rich console used to read terminal width.
        env: Environment mapping checked for COLUMNS and RALPH_FORCE_NARROW.

    Returns:
        'compact' or 'wide'.
    """
    force_narrow = env.get("RALPH_FORCE_NARROW", "").lower().strip() in _RALPH_FORCE_NARROW_TRUTHY
    if force_narrow:
        return "compact"

    width: int
    if hasattr(console, "width") and isinstance(console.width, int) and console.width > 0:
        width = console.width
    elif "COLUMNS" in env:
        try:
            w = int(env["COLUMNS"])
            width = w if w > 0 else 80
        except (ValueError, TypeError):
            width = 80
    else:
        width = 80

    return "compact" if width < NARROW_THRESHOLD else "wide"
