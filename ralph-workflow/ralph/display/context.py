"""Single source of truth for Ralph CLI display dependencies.

No renderer may construct its own Console. All display code must receive
a DisplayContext (or build one via make_display_context) that owns the
console, theme, terminal width, color policy, mode, and adaptive limits.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from ralph.display.mode import NARROW_THRESHOLD
from ralph.display.theme import RALPH_THEME, make_console

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rich.console import Console
    from rich.theme import Theme

_COMPACT_HEADLINE_MAX_CHARS: Final[int] = 80
_WIDE_HEADLINE_MAX_CHARS: Final[int] = 120
_COMPACT_CONDENSER_SOFT_LIMIT: Final[int] = 240
_WIDE_CONDENSER_SOFT_LIMIT: Final[int] = 400
_COMPACT_CONDENSER_HARD_LIMIT: Final[int] = 2400
_WIDE_CONDENSER_HARD_LIMIT: Final[int] = 4000
_COMPACT_STREAMING_CHECKPOINT_CHARS: Final[int] = 2400
_WIDE_STREAMING_CHECKPOINT_CHARS: Final[int] = 4000
_COMPACT_THINKING_PREVIEW_MIN_CHARS: Final[int] = 60
_WIDE_THINKING_PREVIEW_MIN_CHARS: Final[int] = 80
_COMPACT_TOOL_RESULT_HEADLINE_MIN_CHARS: Final[int] = 60
_WIDE_TOOL_RESULT_HEADLINE_MIN_CHARS: Final[int] = 80


@dataclass(frozen=True)
class DisplayContext:
    """Immutable container for all display configuration and dependencies.

    This is the single source of truth for display behavior. No renderer
    may construct its own Console. Obtain one via make_display_context().
    """

    console: Console
    theme: Theme
    width: int
    mode: Literal["compact", "wide"]
    narrow: bool
    color_enabled: bool
    headline_max_chars: int
    condenser_soft_limit: int
    condenser_hard_limit: int
    streaming_checkpoint_chars: int
    thinking_preview_min_chars: int
    tool_result_headline_min_chars: int


def make_display_context(
    *,
    env: Mapping[str, str] | None = None,
    console: Console | None = None,
    force_width: int | None = None,
    force_mode: Literal["compact", "wide"] | None = None,
) -> DisplayContext:
    """Create a DisplayContext with resolved terminal metrics and adaptive limits.

    Args:
        env: Environment mapping (defaults to os.environ).
        console: Console to use (defaults to make_console() with env-aware color policy).
        force_width: Override terminal width detection.
        force_mode: Override mode detection ('compact' or 'wide').

    Returns:
        Fully initialised DisplayContext.
    """
    resolved_env = dict(os.environ if env is None else env)

    if console is None:
        no_color_in_env = "NO_COLOR" in resolved_env
        force_color_in_env = "FORCE_COLOR" in resolved_env
        if no_color_in_env:
            console = make_console(no_color=True, force_terminal=False)
        elif force_color_in_env:
            console = make_console(no_color=False, force_terminal=True)
        else:
            console = make_console()

    # Compute effective width: force_width > COLUMNS env > console.width > 80
    if force_width is not None and force_width > 0:
        width = force_width
    elif "COLUMNS" in resolved_env:
        try:
            w = int(resolved_env["COLUMNS"])
            width = w if w > 0 else (console.width or 80)
        except (ValueError, TypeError):
            width = console.width or 80
    else:
        width = console.width or 80

    # Compute mode: force_mode > RALPH_FORCE_NARROW > width-based detection
    if force_mode is not None:
        mode: Literal["compact", "wide"] = force_mode
    else:
        force_narrow_val = resolved_env.get("RALPH_FORCE_NARROW", "").lower().strip()
        force_narrow = force_narrow_val in {"1", "true", "yes", "on"}
        mode = "compact" if (force_narrow or width < NARROW_THRESHOLD) else "wide"

    narrow = mode == "compact"

    # Color enabled: NO_COLOR wins over FORCE_COLOR per CLI conventions
    color_enabled = "NO_COLOR" not in resolved_env

    # Adaptive limits based on mode
    if mode == "compact":
        headline_max_chars = _COMPACT_HEADLINE_MAX_CHARS
        condenser_soft_limit = _COMPACT_CONDENSER_SOFT_LIMIT
        condenser_hard_limit = _COMPACT_CONDENSER_HARD_LIMIT
        streaming_checkpoint_chars = _COMPACT_STREAMING_CHECKPOINT_CHARS
        thinking_preview_min_chars = _COMPACT_THINKING_PREVIEW_MIN_CHARS
        tool_result_headline_min_chars = _COMPACT_TOOL_RESULT_HEADLINE_MIN_CHARS
    else:
        headline_max_chars = _WIDE_HEADLINE_MAX_CHARS
        condenser_soft_limit = _WIDE_CONDENSER_SOFT_LIMIT
        condenser_hard_limit = _WIDE_CONDENSER_HARD_LIMIT
        streaming_checkpoint_chars = _WIDE_STREAMING_CHECKPOINT_CHARS
        thinking_preview_min_chars = _WIDE_THINKING_PREVIEW_MIN_CHARS
        tool_result_headline_min_chars = _WIDE_TOOL_RESULT_HEADLINE_MIN_CHARS

    return DisplayContext(
        console=console,
        theme=RALPH_THEME,
        width=width,
        mode=mode,
        narrow=narrow,
        color_enabled=color_enabled,
        headline_max_chars=headline_max_chars,
        condenser_soft_limit=condenser_soft_limit,
        condenser_hard_limit=condenser_hard_limit,
        streaming_checkpoint_chars=streaming_checkpoint_chars,
        thinking_preview_min_chars=thinking_preview_min_chars,
        tool_result_headline_min_chars=tool_result_headline_min_chars,
    )


__all__ = ["DisplayContext", "make_display_context"]
