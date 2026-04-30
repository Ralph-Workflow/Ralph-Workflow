"""Single source of truth for Ralph CLI display dependencies.

No renderer may construct its own Console. All display code must receive
a DisplayContext (or build one via make_display_context) that owns the
console, theme, terminal width, color policy, mode, and adaptive limits.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from ralph.display.mode import MEDIUM_THRESHOLD, NARROW_THRESHOLD
from ralph.display.theme import RALPH_THEME, make_console

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rich.console import Console
    from rich.theme import Theme

_COMPACT_HEADLINE_MAX_CHARS: Final[int] = 80
_MEDIUM_HEADLINE_MAX_CHARS: Final[int] = 100
_WIDE_HEADLINE_MAX_CHARS: Final[int] = 120

_COMPACT_CONDENSER_SOFT_LIMIT: Final[int] = 240
_MEDIUM_CONDENSER_SOFT_LIMIT: Final[int] = 300
_WIDE_CONDENSER_SOFT_LIMIT: Final[int] = 400

_COMPACT_CONDENSER_HARD_LIMIT: Final[int] = 2400
_MEDIUM_CONDENSER_HARD_LIMIT: Final[int] = 3200
_WIDE_CONDENSER_HARD_LIMIT: Final[int] = 4000

_COMPACT_STREAMING_CHECKPOINT_CHARS: Final[int] = 2400
_MEDIUM_STREAMING_CHECKPOINT_CHARS: Final[int] = 3200
_WIDE_STREAMING_CHECKPOINT_CHARS: Final[int] = 4000

_COMPACT_THINKING_PREVIEW_MIN_CHARS: Final[int] = 60
_MEDIUM_THINKING_PREVIEW_MIN_CHARS: Final[int] = 70
_WIDE_THINKING_PREVIEW_MIN_CHARS: Final[int] = 80

_COMPACT_TOOL_RESULT_HEADLINE_MIN_CHARS: Final[int] = 60
_MEDIUM_TOOL_RESULT_HEADLINE_MIN_CHARS: Final[int] = 70
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
    mode: Literal["compact", "medium", "wide"]
    narrow: bool
    color_enabled: bool
    headline_max_chars: int
    condenser_soft_limit: int
    condenser_hard_limit: int
    streaming_checkpoint_chars: int
    thinking_preview_min_chars: int
    tool_result_headline_min_chars: int


def _console_has_no_color(console: Console) -> bool:
    """Return True when the console has color disabled via its no_color attribute."""
    raw: object = getattr(console, "no_color", False)
    return bool(raw)


def _build_console(resolved_env: dict[str, str]) -> Console:
    """Create a console based on NO_COLOR / FORCE_COLOR env flags."""
    if "NO_COLOR" in resolved_env:
        return make_console(no_color=True, force_terminal=False)
    if "FORCE_COLOR" in resolved_env:
        return make_console(no_color=False, force_terminal=True)
    return make_console()


def _compute_width(
    resolved_env: dict[str, str],
    console: Console,
    force_width: int | None,
) -> int:
    """Resolve effective terminal width from overrides, env, and console."""
    if force_width is not None and force_width > 0:
        return force_width
    if "COLUMNS" in resolved_env:
        try:
            w = int(resolved_env["COLUMNS"])
            return w if w > 0 else (console.width or 80)
        except (ValueError, TypeError):
            pass
    return console.width or 80


def _compute_mode(
    resolved_env: dict[str, str],
    force_mode: Literal["compact", "medium", "wide"] | None,
    width: int,
) -> Literal["compact", "medium", "wide"]:
    """Resolve display mode from overrides, env flags, and terminal width."""
    if force_mode is not None:
        return force_mode
    force_narrow_val = resolved_env.get("RALPH_FORCE_NARROW", "").lower().strip()
    if force_narrow_val in {"1", "true", "yes", "on"} or width < NARROW_THRESHOLD:
        return "compact"
    if width < MEDIUM_THRESHOLD:
        return "medium"
    return "wide"


@dataclass(frozen=True)
class _ModeAdaptiveLimits:
    headline_max_chars: int
    condenser_soft_limit: int
    condenser_hard_limit: int
    streaming_checkpoint_chars: int
    thinking_preview_min_chars: int
    tool_result_headline_min_chars: int


_MODE_LIMITS: Final[dict[str, _ModeAdaptiveLimits]] = {
    "compact": _ModeAdaptiveLimits(
        headline_max_chars=_COMPACT_HEADLINE_MAX_CHARS,
        condenser_soft_limit=_COMPACT_CONDENSER_SOFT_LIMIT,
        condenser_hard_limit=_COMPACT_CONDENSER_HARD_LIMIT,
        streaming_checkpoint_chars=_COMPACT_STREAMING_CHECKPOINT_CHARS,
        thinking_preview_min_chars=_COMPACT_THINKING_PREVIEW_MIN_CHARS,
        tool_result_headline_min_chars=_COMPACT_TOOL_RESULT_HEADLINE_MIN_CHARS,
    ),
    "medium": _ModeAdaptiveLimits(
        headline_max_chars=_MEDIUM_HEADLINE_MAX_CHARS,
        condenser_soft_limit=_MEDIUM_CONDENSER_SOFT_LIMIT,
        condenser_hard_limit=_MEDIUM_CONDENSER_HARD_LIMIT,
        streaming_checkpoint_chars=_MEDIUM_STREAMING_CHECKPOINT_CHARS,
        thinking_preview_min_chars=_MEDIUM_THINKING_PREVIEW_MIN_CHARS,
        tool_result_headline_min_chars=_MEDIUM_TOOL_RESULT_HEADLINE_MIN_CHARS,
    ),
    "wide": _ModeAdaptiveLimits(
        headline_max_chars=_WIDE_HEADLINE_MAX_CHARS,
        condenser_soft_limit=_WIDE_CONDENSER_SOFT_LIMIT,
        condenser_hard_limit=_WIDE_CONDENSER_HARD_LIMIT,
        streaming_checkpoint_chars=_WIDE_STREAMING_CHECKPOINT_CHARS,
        thinking_preview_min_chars=_WIDE_THINKING_PREVIEW_MIN_CHARS,
        tool_result_headline_min_chars=_WIDE_TOOL_RESULT_HEADLINE_MIN_CHARS,
    ),
}


def make_display_context(
    *,
    env: Mapping[str, str] | None = None,
    console: Console | None = None,
    force_width: int | None = None,
    force_mode: Literal["compact", "medium", "wide"] | None = None,
) -> DisplayContext:
    """Create a DisplayContext with resolved terminal metrics and adaptive limits.

    Args:
        env: Environment mapping (defaults to os.environ).
        console: Console to use (defaults to make_console() with env-aware color policy).
        force_width: Override terminal width detection.
        force_mode: Override mode detection ('compact', 'medium', or 'wide').

    Returns:
        Fully initialised DisplayContext.
    """
    resolved_env = dict(os.environ if env is None else env)
    resolved_console = console if console is not None else _build_console(resolved_env)
    width = _compute_width(resolved_env, resolved_console, force_width)
    mode = _compute_mode(resolved_env, force_mode, width)
    limits = _MODE_LIMITS.get(mode, _MODE_LIMITS["wide"])
    # NO_COLOR wins over FORCE_COLOR per CLI conventions.
    color_enabled = "NO_COLOR" not in resolved_env and not _console_has_no_color(resolved_console)
    return DisplayContext(
        console=resolved_console,
        theme=RALPH_THEME,
        width=width,
        mode=mode,
        narrow=mode == "compact",
        color_enabled=color_enabled,
        headline_max_chars=limits.headline_max_chars,
        condenser_soft_limit=limits.condenser_soft_limit,
        condenser_hard_limit=limits.condenser_hard_limit,
        streaming_checkpoint_chars=limits.streaming_checkpoint_chars,
        thinking_preview_min_chars=limits.thinking_preview_min_chars,
        tool_result_headline_min_chars=limits.tool_result_headline_min_chars,
    )


__all__ = ["DisplayContext", "make_display_context"]
