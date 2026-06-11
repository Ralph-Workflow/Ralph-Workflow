"""Single source of truth for Ralph CLI display dependencies.

No renderer may construct its own Console. All display code must receive
a DisplayContext (or build one via make_display_context) that owns the
console, theme, terminal width, color policy, mode, and adaptive limits.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sys
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal

from ralph.display._mode_adaptive_limits import _ModeAdaptiveLimits
from ralph.display._resolved_env import _ResolvedEnv
from ralph.display.mode import MEDIUM_THRESHOLD, NARROW_THRESHOLD
from ralph.display.theme import (
    ASCII_GLYPHS,
    RALPH_THEME,
    UNICODE_GLYPHS,
    detect_glyph_capability,
    make_console,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from rich.console import Console
    from rich.theme import Theme

COMPACT_HEADLINE_MAX_CHARS: Final[int] = 80
_MEDIUM_HEADLINE_MAX_CHARS: Final[int] = 100
WIDE_HEADLINE_MAX_CHARS: Final[int] = 120

COMPACT_CONDENSER_SOFT_LIMIT: Final[int] = 240
_MEDIUM_CONDENSER_SOFT_LIMIT: Final[int] = 300
WIDE_CONDENSER_SOFT_LIMIT: Final[int] = 400

COMPACT_CONDENSER_HARD_LIMIT: Final[int] = 2400
_MEDIUM_CONDENSER_HARD_LIMIT: Final[int] = 3200
WIDE_CONDENSER_HARD_LIMIT: Final[int] = 4000

COMPACT_STREAMING_CHECKPOINT_CHARS: Final[int] = 2400
_MEDIUM_STREAMING_CHECKPOINT_CHARS: Final[int] = 3200
WIDE_STREAMING_CHECKPOINT_CHARS: Final[int] = 4000

COMPACT_THINKING_PREVIEW_MIN_CHARS: Final[int] = 60
_MEDIUM_THINKING_PREVIEW_MIN_CHARS: Final[int] = 70
WIDE_THINKING_PREVIEW_MIN_CHARS: Final[int] = 80

COMPACT_TOOL_RESULT_HEADLINE_MIN_CHARS: Final[int] = 60
_MEDIUM_TOOL_RESULT_HEADLINE_MIN_CHARS: Final[int] = 70
WIDE_TOOL_RESULT_HEADLINE_MIN_CHARS: Final[int] = 80

_STREAMING_CHECKPOINT_FRAGMENTS: Final[int] = 20

_STREAMING_DEDUP_DISABLED_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off"})
_STREAMING_CHECKPOINTS_DISABLED_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off"})

_RALPH_FORCE_NARROW_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})
_RALPH_FORCE_ASCII_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})


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

    force_ascii_val = env.get("RALPH_FORCE_ASCII", "").lower().strip()
    force_ascii = force_ascii_val in _RALPH_FORCE_ASCII_TRUTHY

    columns: int | None = None
    if "COLUMNS" in env:
        try:
            w = int(env["COLUMNS"])
            columns = w if w > 0 else None
        except (ValueError, TypeError):
            pass

    streaming_dedup_val = env.get("RALPH_STREAMING_DEDUP", "").lower().strip()
    streaming_dedup_enabled = streaming_dedup_val not in _STREAMING_DEDUP_DISABLED_VALUES

    streaming_checkpoints_val = env.get("RALPH_STREAMING_CHECKPOINTS", "").lower().strip()
    streaming_checkpoints_enabled = (
        streaming_checkpoints_val not in _STREAMING_CHECKPOINTS_DISABLED_VALUES
    )

    return _ResolvedEnv(
        no_color=no_color,
        force_color=force_color,
        force_narrow=force_narrow,
        columns=columns,
        force_ascii=force_ascii,
        streaming_dedup_enabled=streaming_dedup_enabled,
        streaming_checkpoints_enabled=streaming_checkpoints_enabled,
    )


def _console_has_no_color(console: Console, *, injected_console: bool) -> bool:
    """Return True when the console has color disabled via its no_color attribute."""
    raw: object = getattr(console, "no_color", False)
    if not bool(raw):
        return False
    if not injected_console:
        return True
    if _console_has_forced_color(console):
        return False
    raw_file: object = getattr(console, "_file", None)
    return raw_file is not None


def _console_has_forced_color(console: Console) -> bool:
    """Return True when an injected console explicitly requests terminal color."""
    force_terminal_raw: object = getattr(console, "_force_terminal", None)
    color_system_raw: object = getattr(console, "_color_system", None)
    return force_terminal_raw is True and color_system_raw is not None


def _normalize_injected_console_color(console: Console, resolved_env: _ResolvedEnv) -> None:
    """Make injected Rich consoles honor Ralph's env-driven color contract."""
    if resolved_env.no_color:
        with contextlib.suppress(Exception):
            console.no_color = True
        return
    if _console_has_forced_color(console):
        with contextlib.suppress(Exception):
            console.no_color = False


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
        glyphs_enabled: True when Unicode glyphs should be used, False for ASCII fallbacks.
        headline_max_chars: Max characters for condensed headlines.
        condenser_soft_limit: Soft limit for content condensation.
        condenser_hard_limit: Hard limit for content condensation.
        streaming_checkpoint_chars: Chars between streaming checkpoints.
        streaming_checkpoint_fragments: Emit checkpoint every N fragments.
        streaming_dedup_enabled: Whether to deduplicate consecutive identical fragments.
        streaming_checkpoints_enabled: Whether to emit streaming checkpoints.
        thinking_preview_min_chars: Min chars for thinking preview.
        tool_result_headline_min_chars: Min chars for tool result headline.
    """

    console: Console
    theme: Theme
    width: int
    mode: Literal["compact", "medium", "wide"]
    narrow: bool
    color_enabled: bool
    glyphs_enabled: bool
    headline_max_chars: int
    condenser_soft_limit: int
    condenser_hard_limit: int
    streaming_checkpoint_chars: int
    streaming_checkpoint_fragments: int
    streaming_dedup_enabled: bool
    streaming_checkpoints_enabled: bool
    thinking_preview_min_chars: int
    tool_result_headline_min_chars: int
    # Captured env mapping used to resolve flags; excluded from equality and hash
    env: Mapping[str, str] = field(default_factory=dict, compare=False, repr=False)
    # Stored overrides for refreshed() — excluded from equality and hash
    _resolved_env: _ResolvedEnv = field(
        default_factory=lambda: _ResolvedEnv(
            no_color=False,
            force_color=False,
            force_narrow=False,
            columns=None,
            force_ascii=False,
            streaming_dedup_enabled=True,
            streaming_checkpoints_enabled=True,
        ),
        repr=False,
        compare=False,
    )
    _force_width: int | None = field(default=None, repr=False, compare=False)
    _force_mode: Literal["compact", "medium", "wide"] | None = field(
        default=None, repr=False, compare=False
    )
    _force_glyphs: bool | None = field(default=None, repr=False, compare=False)

    def glyph_for(self, name: str) -> str:
        """Return the glyph string for the given logical name.

        Args:
            name: Logical glyph name (e.g., 'success', 'error', 'milestone', 'arrow').

        Returns:
            Unicode glyph when glyphs_enabled is True, ASCII fallback otherwise.

        Raises:
            KeyError: If name is not a known glyph key.
        """
        if name not in UNICODE_GLYPHS:
            known = ", ".join(sorted(UNICODE_GLYPHS))
            raise KeyError(f"Unknown glyph {name!r}. Known glyphs: {known}")
        if self.glyphs_enabled:
            return UNICODE_GLYPHS[name]
        return ASCII_GLYPHS[name]

    def refreshed(self) -> DisplayContext:
        """Return a new DisplayContext with refreshed terminal width and derived limits.

        Re-resolves width and mode using the same precedence rules as make_display_context(),
        preserving any active overrides (RALPH_FORCE_NARROW, COLUMNS, force_width, force_mode)
        stored at construction time. The console identity, theme, color_enabled,
        and glyphs_enabled are unchanged.

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
            glyphs_enabled=self.glyphs_enabled,
            headline_max_chars=new_limits.headline_max_chars,
            condenser_soft_limit=new_limits.condenser_soft_limit,
            condenser_hard_limit=new_limits.condenser_hard_limit,
            streaming_checkpoint_chars=new_limits.streaming_checkpoint_chars,
            streaming_checkpoint_fragments=self.streaming_checkpoint_fragments,
            streaming_dedup_enabled=self.streaming_dedup_enabled,
            streaming_checkpoints_enabled=self.streaming_checkpoints_enabled,
            thinking_preview_min_chars=new_limits.thinking_preview_min_chars,
            tool_result_headline_min_chars=new_limits.tool_result_headline_min_chars,
            _resolved_env=self._resolved_env,
            env=self.env,
            _force_width=self._force_width,
            _force_mode=self._force_mode,
            _force_glyphs=self._force_glyphs,
        )


_MODE_LIMITS: Final[dict[str, _ModeAdaptiveLimits]] = {
    "compact": _ModeAdaptiveLimits(
        headline_max_chars=COMPACT_HEADLINE_MAX_CHARS,
        condenser_soft_limit=COMPACT_CONDENSER_SOFT_LIMIT,
        condenser_hard_limit=COMPACT_CONDENSER_HARD_LIMIT,
        streaming_checkpoint_chars=COMPACT_STREAMING_CHECKPOINT_CHARS,
        thinking_preview_min_chars=COMPACT_THINKING_PREVIEW_MIN_CHARS,
        tool_result_headline_min_chars=COMPACT_TOOL_RESULT_HEADLINE_MIN_CHARS,
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
        headline_max_chars=WIDE_HEADLINE_MAX_CHARS,
        condenser_soft_limit=WIDE_CONDENSER_SOFT_LIMIT,
        condenser_hard_limit=WIDE_CONDENSER_HARD_LIMIT,
        streaming_checkpoint_chars=WIDE_STREAMING_CHECKPOINT_CHARS,
        thinking_preview_min_chars=WIDE_THINKING_PREVIEW_MIN_CHARS,
        tool_result_headline_min_chars=WIDE_TOOL_RESULT_HEADLINE_MIN_CHARS,
    ),
}


def make_display_context(
    *,
    env: Mapping[str, str] | None = None,
    console: Console | None = None,
    force_width: int | None = None,
    force_mode: Literal["compact", "medium", "wide"] | None = None,
    force_glyphs: bool | None = None,
) -> DisplayContext:
    """Create a DisplayContext with resolved terminal metrics and adaptive limits.

    Args:
        env: Environment mapping (defaults to os.environ).
        console: Console to use (defaults to make_console() with env-aware color policy).
        force_width: Override terminal width detection.
        force_mode: Override mode detection ('compact', 'medium', or 'wide').
        force_glyphs: Override glyph detection (True=Unicode, False=ASCII, None=auto-detect).

    Returns:
        Fully initialised DisplayContext.
    """
    env_was_provided = env is not None
    env_dict: dict[str, str] = dict(os.environ if env is None else env)
    resolved_env = _resolve_env(env_dict)
    if console is None:
        injected_console = False
        resolved_console = _build_console(resolved_env)
    else:
        injected_console = True
        resolved_console = console
        _normalize_injected_console_color(resolved_console, resolved_env)
    width = _compute_width(resolved_env, resolved_console, force_width)
    mode = _compute_mode(resolved_env, force_mode, width)
    limits = _MODE_LIMITS.get(mode, _MODE_LIMITS["wide"])

    # NO_COLOR wins over FORCE_COLOR per CLI conventions.
    color_enabled = not resolved_env.no_color and not _console_has_no_color(
        resolved_console,
        injected_console=injected_console,
    )

    # Glyph capability detection: force_glyphs > RALPH_FORCE_ASCII > stream encoding > TERM=dumb
    if force_glyphs is not None:
        glyphs_enabled = force_glyphs
    elif injected_console and not env_was_provided and not resolved_env.force_ascii:
        glyphs_enabled = True
    else:
        glyph_file: object = resolved_console.file
        glyphs_enabled = detect_glyph_capability(
            glyph_file if glyph_file is not None else sys.stdout,
            env_dict,
        )

    return DisplayContext(
        console=resolved_console,
        theme=RALPH_THEME,
        width=width,
        mode=mode,
        narrow=mode == "compact",
        color_enabled=color_enabled,
        glyphs_enabled=glyphs_enabled,
        headline_max_chars=limits.headline_max_chars,
        condenser_soft_limit=limits.condenser_soft_limit,
        condenser_hard_limit=limits.condenser_hard_limit,
        streaming_checkpoint_chars=limits.streaming_checkpoint_chars,
        streaming_checkpoint_fragments=_STREAMING_CHECKPOINT_FRAGMENTS,
        streaming_dedup_enabled=resolved_env.streaming_dedup_enabled,
        streaming_checkpoints_enabled=resolved_env.streaming_checkpoints_enabled,
        thinking_preview_min_chars=limits.thinking_preview_min_chars,
        tool_result_headline_min_chars=limits.tool_result_headline_min_chars,
        env=env_dict,
        _resolved_env=resolved_env,
        _force_width=force_width,
        _force_mode=force_mode,
        _force_glyphs=force_glyphs,
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


def install_poll_refresher(
    ctx_holder: list[DisplayContext],
    interval_seconds: float = 2.0,
    on_refresh: Callable[[DisplayContext], None] | None = None,
) -> Callable[[], None]:
    """Start a daemon thread that periodically refreshes DisplayContext.

    This provides a fallback for non-POSIX platforms (Windows) where SIGWINCH
    is not available, or when called from a non-main thread.

    Args:
        ctx_holder: A single-element list whose 0th element is the DisplayContext
            to refresh periodically. The thread replaces ctx_holder[0] with
            ctx_holder[0].refreshed() every interval_seconds.
        interval_seconds: How often to refresh (default 2.0s).
        on_refresh: Optional callback invoked with the refreshed context after
            ctx_holder[0] is replaced.

    Returns:
        A stop() callable that signals the thread to exit and joins it (1s timeout).
    """
    stop_event = threading.Event()

    def poll_loop() -> None:
        while not stop_event.wait(interval_seconds):
            refreshed = ctx_holder[0].refreshed()
            ctx_holder[0] = refreshed
            if on_refresh is not None:
                on_refresh(refreshed)

    thread = threading.Thread(target=poll_loop, daemon=True)
    thread.start()

    def stop() -> None:
        stop_event.set()
        thread.join(timeout=1.0)

    return stop


def install_width_refresher(
    ctx_holder: list[DisplayContext],
    on_refresh: Callable[[DisplayContext], None] | None = None,
) -> Callable[[], None]:
    """Install a width refresher using the best available strategy.

    On POSIX main thread: uses SIGWINCH signal handler (install_sigwinch_refresher).
    On Windows or non-main thread: falls back to poll-based refresher (install_poll_refresher).

    Args:
        ctx_holder: A single-element list whose 0th element is the DisplayContext
            to refresh on resize.
        on_refresh: Optional callback invoked with the refreshed context after
            ctx_holder[0] is replaced.

    Returns:
        A stop() callable (for poll-based refresher; SIGWINCH handler has no cleanup).
    """
    if sys.platform != "win32" and threading.main_thread() is threading.current_thread():
        install_sigwinch_refresher(ctx_holder, on_refresh)
        # SIGWINCH handler cannot be uninstalled, return no-op stop
        return lambda: None
    return install_poll_refresher(ctx_holder, interval_seconds=2.0, on_refresh=on_refresh)


__all__ = [
    "DisplayContext",
    "install_poll_refresher",
    "install_sigwinch_refresher",
    "install_width_refresher",
    "make_display_context",
]
