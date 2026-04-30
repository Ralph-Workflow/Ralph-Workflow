"""Terminal mode detection for Ralph's transcript output."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from rich.console import Console

NARROW_THRESHOLD: int = 60
MEDIUM_THRESHOLD: int = 100

_RALPH_FORCE_NARROW_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def detect_mode(
    console: Console,
    env: dict[str, str],
) -> Literal["compact", "medium", "wide"]:
    """Detect display mode from terminal width and environment.

    Returns 'compact' when the terminal is narrower than NARROW_THRESHOLD or
    RALPH_FORCE_NARROW is set to a truthy value. Returns 'medium' for widths in
    [NARROW_THRESHOLD, MEDIUM_THRESHOLD). Returns 'wide' for MEDIUM_THRESHOLD and above.

    Precedence for width resolution: COLUMNS env > console.width > 80.

    Args:
        console: Rich console used to read terminal width.
        env: Environment mapping checked for COLUMNS and RALPH_FORCE_NARROW.

    Returns:
        'compact', 'medium', or 'wide'.
    """
    force_narrow = env.get("RALPH_FORCE_NARROW", "").lower().strip() in _RALPH_FORCE_NARROW_TRUTHY
    if force_narrow:
        return "compact"

    # COLUMNS takes precedence over console.width (matches make_display_context precedence)
    width: int
    if "COLUMNS" in env:
        try:
            w = int(env["COLUMNS"])
            width = w if w > 0 else 80
        except (ValueError, TypeError):
            width = 80
    elif hasattr(console, "width") and isinstance(console.width, int) and console.width > 0:
        width = console.width
    else:
        width = 80

    if width < NARROW_THRESHOLD:
        return "compact"
    if width < MEDIUM_THRESHOLD:
        return "medium"
    return "wide"
