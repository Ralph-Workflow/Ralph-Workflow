"""Single source of truth for Ralph CLI display dependencies.

No renderer may construct its own Console. All display code must receive
a DisplayContext (or build one via make_display_context) that owns the
console, theme, terminal width, color policy, mode, and adaptive limits.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal

from ralph.display.mode import MEDIUM_THRESHOLD, NARROW_THRESHOLD
from ralph.display.theme import RALPH_THEME, make_console

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

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
class _ResolvedEnv:
    """Resolved environment settings for display configuration.

    Attributes:
        no_color: True when NO_COLOR is present in environment.
        force_color: True when FORCE_COLOR is present in environment.
        force_narrow: True when RALPH_FORCE_NARROW is set to a truthy value.
        columns: Terminal width override from COLUMNS env, or None.
    """

    no_color: bool
    force_color: bool
    force_narrow: bool
    columns: int | None


_RALPH_FORCE_NARROW_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def _resolve_env(env: Mapping[str, str]) -> _ResolvedEnv:
    """Parse environment variables into resolved display settings.

    Args:
        env: Environment mapping to parse.

    Returns:
        _ResolvedEnv with all display-relevant env settings resolved.
    """
    no_color = "NO_COLOR" in env
    force_color = "FORCE_COLOR" in env
    force_narrow_val = env.get("RALPH_FORCE_NARROW", "").lower().strip()
    force_narrow = force_narrow_val in _RALPH_FORCE_NARROW_TRUTHY

    columns: int | None = None
    if "COLUMNS" in env:
        try:
            w = int(env["COLUMNS"])
            columns = w if w > 0 else None
        except (ValueError, TypeError):
            pass

    return _ResolvedEnv(
        no_color=no_color,
        force_color=force_color,
        force_narrow=force_narrow,
        columns=columns,
    )


@dataclass(frozen=True)
class DisplayContext:
    """Immutable container for all display configuration and dependencies.

    This is the single source of truth for display behavior. No renderer
    may construct its own Console. Obtain one via make_display_context().

    Attributes:
        console: Rich Console instance for all rendering.
        theme: Rich Theme with Ralph's Okabe-Ito color palette.
        width: Effective terminal width in characters.
        mode: Display mode - 'compact', 'medium', or 'wide'.
        narrow: True when mode is 'compact'.
        color_enabled: True when color output is enabled.
        headline_max_chars: Max characters for condensed headlines.
        condenser_soft_limit: Soft limit for content condensation.
        condenser_hard_limit: Hard limit for content condensation.
        streaming_checkpoint_chars: Chars between streaming checkpoints.
        thinking_preview_min_chars: Min chars for thinking preview.
        tool_result_headline_min_chars: Min chars for tool result headline.
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
    # Stored overrides for refreshed() — excluded from equality and hash
    _resolved_env: _ResolvedEnv = field(
        default_factory=lambda: _ResolvedEnv(
            no_color=False, force_color=False, force_narrow=False, columns=None
        ),
        repr=False,
        compare=False,
    )
    _force_width: int | None = field(default=None, repr=False, compare=False)
    _force_mode: Literal["compact", "medium", "wide"] | None = field(
        default=None, repr=False, compare=False
    )

    def refreshed(self) -> DisplayContext:
        """Return a new DisplayContext with refreshed terminal width and derived limits.

        Re-resolves width and mode using the same precedence rules as make_display_context(),
        preserving any active overrides (RALPH_FORCE_NARROW, COLUMNS, force_width, force_mode)
        stored at construction time. The console identity, theme, and color_enabled are
        unchanged.

        Returns:
            New DisplayContext with updated width, mode, and limits.
        """
        new_width = _compute_width(self._resolved_env, self.console, self._force_width)
        new_mode = _compute_mode(self._resolved_env, self._force_mode, new_width)
        new_limits = _MODE_LIMITS.get(new_mode, _MODE_LIMITS["wide"])

        return DisplayContext(
            console=self.console,
            theme=self.theme,
            width=new_width,
            mode=new_mode,
            narrow=new_mode == "compact",
            color_enabled=self.color_enabled,
            headline_max_chars=new_limits.headline_max_chars,
            condenser_soft_limit=new_limits.condenser_soft_limit,
            condenser_hard_limit=new_limits.condenser_hard_limit,
            streaming_checkpoint_chars=new_limits.streaming_checkpoint_chars,
            thinking_preview_min_chars=new_limits.thinking_preview_min_chars,
            tool_result_headline_min_chars=new_limits.tool_result_headline_min_chars,
            _resolved_env=self._resolved_env,
            _force_width=self._force_width,
            _force_mode=self._force_mode,
        )


def _console_has_no_color(console: Console) -> bool:
    """Return True when the console has color disabled via its no_color attribute."""
    raw: object = getattr(console, "no_color", False)
    return bool(raw)


def _build_console(resolved_env: _ResolvedEnv) -> Console:
    """Create a console based on resolved NO_COLOR / FORCE_COLOR settings.

    Args:
        resolved_env: Pre-resolved environment settings.

    Returns:
        Configured Console instance.
    """
    if resolved_env.no_color:
        return make_console(no_color=True, force_terminal=False)
    if resolved_env.force_color:
        return make_console(no_color=False, force_terminal=True)
    return make_console()


def _compute_width(
    resolved_env: _ResolvedEnv,
    console: Console,
    force_width: int | None,
) -> int:
    """Resolve effective terminal width from overrides, env, and console.

    Args:
        resolved_env: Pre-resolved environment settings.
        console: Console to read width from as fallback.
        force_width: Explicit width override.

    Returns:
        Effective terminal width in characters.
    """
    if force_width is not None and force_width > 0:
        return force_width
    if resolved_env.columns is not None:
        return resolved_env.columns
    return console.width or 80


def _compute_mode(
    resolved_env: _ResolvedEnv,
    force_mode: Literal["compact", "medium", "wide"] | None,
    width: int,
) -> Literal["compact", "medium", "wide"]:
    """Resolve display mode from overrides, env flags, and terminal width.

    Args:
        resolved_env: Pre-resolved environment settings.
        force_mode: Explicit mode override.
        width: Effective terminal width.

    Returns:
        Resolved display mode.
    """
    if force_mode is not None:
        return force_mode
    if resolved_env.force_narrow or width < NARROW_THRESHOLD:
        return "compact"
    if width < MEDIUM_THRESHOLD:
        return "medium"
    return "wide"


@dataclass(frozen=True)
class _ModeAdaptiveLimits:
    """Mode-adaptive character limits for content condensation."""

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
    resolved_env = _resolve_env(dict(os.environ if env is None else env))
    resolved_console = console if console is not None else _build_console(resolved_env)
    width = _compute_width(resolved_env, resolved_console, force_width)
    mode = _compute_mode(resolved_env, force_mode, width)
    limits = _MODE_LIMITS.get(mode, _MODE_LIMITS["wide"])
    # NO_COLOR wins over FORCE_COLOR per CLI conventions.
    color_enabled = not resolved_env.no_color and not _console_has_no_color(resolved_console)
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
        _resolved_env=resolved_env,
        _force_width=force_width,
        _force_mode=force_mode,
    )


def install_sigwinch_refresher(
    ctx_holder: list[DisplayContext],
    on_refresh: Callable[[DisplayContext], None] | None = None,
) -> None:
    """Install a SIGWINCH handler that refreshes DisplayContext on terminal resize.

    On POSIX systems, this installs a signal handler that replaces the
    DisplayContext in ctx_holder[0] with a refreshed version that reflects
    the new terminal size. An optional callback can keep any long-lived
    display objects synced with that refreshed context.

    On non-POSIX systems (Windows), this function is a no-op.

    Args:
        ctx_holder: A single-element list whose 0th element is the DisplayContext
            to refresh on SIGWINCH. The handler replaces ctx_holder[0] with
            ctx_holder[0].refreshed().
        on_refresh: Optional callback invoked with the refreshed context after
            ctx_holder[0] is replaced.

    Note:
        This function must be called from the main thread, as signal.signal
        only works in the main thread. If called from a non-main thread,
        the function returns silently without installing the handler.
    """
    if sys.platform == "win32":
        return
    if threading.main_thread() is not threading.current_thread():
        return

    def handler(signum: int, frame: object) -> None:
        refreshed = ctx_holder[0].refreshed()
        ctx_holder[0] = refreshed
        if on_refresh is not None:
            on_refresh(refreshed)

    signal.signal(signal.SIGWINCH, handler)


__all__ = ["DisplayContext", "install_sigwinch_refresher", "make_display_context"]
