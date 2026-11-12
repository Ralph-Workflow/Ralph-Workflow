from __future__ import annotations

import contextlib
import os
import signal
import sys
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal, TextIO, cast

from ralph.display._mode_adaptive_limits import _DEFAULT_LIMITS
from ralph.display._resolved_env import _ResolvedEnv
from ralph.display.mode import DEFAULT_MODE
from ralph.display.theme import (
    ASCII_GLYPHS,
    UNICODE_GLYPHS,
    detect_glyph_capability,
    detect_terminal_background_hex,
    detect_terminal_background_is_light,
    make_console,
    theme_for_background,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from rich.console import Console
    from rich.theme import Theme

    from ralph.interrupt.signal_getter import SignalGetter
    from ralph.interrupt.signal_setter import SignalSetter

_STREAMING_CHECKPOINT_FRAGMENTS: Final[int] = 20

_STREAMING_DEDUP_DISABLED_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off"})
_STREAMING_CHECKPOINTS_DISABLED_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off"})

_RALPH_FORCE_ASCII_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def _resolve_env(env: Mapping[str, str]) -> _ResolvedEnv:
    no_color = bool(env.get("NO_COLOR", "").strip())
    force_color_value = env.get("FORCE_COLOR", "").strip().lower()
    force_color = force_color_value not in {"", "0", "false", "no", "off"}

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
        columns=columns,
        force_ascii=force_ascii,
        streaming_dedup_enabled=streaming_dedup_enabled,
        streaming_checkpoints_enabled=streaming_checkpoints_enabled,
    )


def _console_has_no_color(console: Console, *, injected_console: bool) -> bool:
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
    force_terminal_raw: object = getattr(console, "_force_terminal", None)
    return force_terminal_raw is True


def _normalize_injected_console_color(console: Console, resolved_env: _ResolvedEnv) -> None:
    if resolved_env.no_color:
        with contextlib.suppress(Exception):
            console.no_color = True
        return
    if _console_has_forced_color(console):
        with contextlib.suppress(Exception):
            console.no_color = False
        with contextlib.suppress(Exception):
            if console._color_system is None:
                from rich.color import ColorSystem

                object.__setattr__(console, "_color_system", ColorSystem.TRUECOLOR)


def _build_console(
    resolved_env: _ResolvedEnv,
    terminal_bg_is_light: bool | None,
    surface_hex: str | None = None,
    *,
    output_stream: TextIO | None = None,
) -> Console:
    if resolved_env.no_color:
        return make_console(
            file=output_stream,
            no_color=True,
            force_terminal=False,
            terminal_bg_is_light=terminal_bg_is_light,
            surface_hex=surface_hex,
        )
    if resolved_env.force_color:
        return make_console(
            file=output_stream,
            no_color=False,
            force_terminal=True,
            terminal_bg_is_light=terminal_bg_is_light,
            surface_hex=surface_hex,
        )
    return make_console(
        file=output_stream,
        no_color=False,
        force_terminal=True,
        color_system="truecolor",
        terminal_bg_is_light=terminal_bg_is_light,
        surface_hex=surface_hex,
    )


def _compute_width(
    resolved_env: _ResolvedEnv,
    console: Console,
    force_width: int | None,
    *,
    prefer_configured_width: bool = True,
    injected_console: bool = False,
    explicit_env: bool = False,
) -> int:
    if force_width is not None and force_width > 0:
        return force_width
    return _compute_width_uncached(
        resolved_env,
        console,
        prefer_configured_width=prefer_configured_width,
        injected_console=injected_console,
        explicit_env=explicit_env,
    )


def _compute_width_uncached(
    resolved_env: _ResolvedEnv,
    console: Console,
    *,
    prefer_configured_width: bool,
    injected_console: bool,
    explicit_env: bool,
) -> int:
    if explicit_env and resolved_env.columns is not None:
        return resolved_env.columns
    if injected_console:
        configured_width = cast("object", getattr(console, "_width", None))
        if isinstance(configured_width, int) and configured_width > 0:
            return configured_width
        if console.width > 0:
            return console.width
        return 80
    configured_width = cast("object", getattr(console, "_width", None))
    if prefer_configured_width and isinstance(configured_width, int) and configured_width > 0:
        return configured_width
    return console.width or 80


def _set_injected_console_width(console: Console, width: int) -> None:
    with contextlib.suppress(Exception):
        height = cast("object", getattr(console, "_height", None))
        if not isinstance(height, int) or height <= 0:
            height = console.size.height
        console._width = width
        console._height = height


def _compute_height(console: Console, force_height: int | None) -> int | None:
    if force_height is not None and force_height > 0:
        return force_height
    if force_height is not None:
        return None
    configured_height = cast("object", getattr(console, "_height", None))
    if isinstance(configured_height, int) and configured_height > 0:
        return configured_height
    size_obj: object = getattr(console, "size", None)
    if size_obj is not None:
        candidate: object = getattr(size_obj, "height", None)
        if isinstance(candidate, int) and candidate > 0:
            return candidate
    return None


def _compute_default_mode() -> Literal["default"]:
    return cast("Literal['default']", DEFAULT_MODE)


@dataclass(frozen=True)
class DisplayContext:
    console: Console
    theme: Theme
    width: int
    mode: Literal["default"]
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
    body_measure_cap: int = 100
    terminal_background_is_light: bool | None = None
    terminal_background_hex: str | None = None
    height: int | None = None
    env: Mapping[str, str] = field(default_factory=dict, compare=False, repr=False)
    _resolved_env: _ResolvedEnv = field(
        default_factory=lambda: _ResolvedEnv(
            no_color=False,
            force_color=False,
            columns=None,
            force_ascii=False,
            streaming_dedup_enabled=True,
            streaming_checkpoints_enabled=True,
        ),
        repr=False,
        compare=False,
    )
    _force_width: int | None = field(default=None, repr=False, compare=False)
    _force_glyphs: bool | None = field(default=None, repr=False, compare=False)
    _force_height: int | None = field(default=None, repr=False, compare=False)
    _explicit_env: bool = field(default=False, repr=False, compare=False)

    def glyph_for(self, name: str) -> str:
        if name not in UNICODE_GLYPHS:
            known = ", ".join(sorted(UNICODE_GLYPHS))
            raise KeyError(f"Unknown glyph {name!r}. Known glyphs: {known}")
        if self.glyphs_enabled:
            return UNICODE_GLYPHS[name]
        return ASCII_GLYPHS[name]

    def is_height_constrained(self, threshold: int = 12) -> bool:
        if self.height is None:
            return False
        return self.height <= threshold

    def body_measure(self) -> int:
        return max(40, min(self.width, self.body_measure_cap))

    def refreshed(self) -> DisplayContext:
        new_width = _compute_width(
            self._resolved_env,
            self.console,
            self._force_width,
            prefer_configured_width=False,
            explicit_env=self._explicit_env,
        )
        new_height = _compute_height(self.console, self._force_height)

        return DisplayContext(
            console=self.console,
            theme=self.theme,
            width=new_width,
            mode=cast("Literal['default']", DEFAULT_MODE),
            color_enabled=self.color_enabled,
            glyphs_enabled=self.glyphs_enabled,
            headline_max_chars=self.headline_max_chars,
            condenser_soft_limit=self.condenser_soft_limit,
            condenser_hard_limit=self.condenser_hard_limit,
            streaming_checkpoint_chars=self.streaming_checkpoint_chars,
            streaming_checkpoint_fragments=self.streaming_checkpoint_fragments,
            streaming_dedup_enabled=self.streaming_dedup_enabled,
            streaming_checkpoints_enabled=self.streaming_checkpoints_enabled,
            thinking_preview_min_chars=self.thinking_preview_min_chars,
            tool_result_headline_min_chars=self.tool_result_headline_min_chars,
            body_measure_cap=self.body_measure_cap,
            terminal_background_is_light=self.terminal_background_is_light,
            terminal_background_hex=self.terminal_background_hex,
            height=new_height,
            _resolved_env=self._resolved_env,
            env=self.env,
            _force_width=self._force_width,
            _force_glyphs=self._force_glyphs,
            _force_height=self._force_height,
            _explicit_env=self._explicit_env,
        )


def make_display_context(
    *,
    env: Mapping[str, str] | None = None,
    console: Console | None = None,
    output_stream: TextIO | None = None,
    force_width: int | None = None,
    force_glyphs: bool | None = None,
    force_height: int | None = None,
) -> DisplayContext:
    if console is not None and output_stream is not None:
        msg = "output_stream cannot be combined with an injected console"
        raise ValueError(msg)
    env_was_provided = env is not None
    env_dict: dict[str, str] = dict(os.environ if env is None else env)
    resolved_env = _resolve_env(env_dict)
    resolved_background: bool | None = None
    resolved_background_hex: str | None = None
    with contextlib.suppress(Exception):
        resolved_background = detect_terminal_background_is_light(env_dict)
        resolved_background_hex = detect_terminal_background_hex(env_dict)
    if console is None:
        injected_console = False
        resolved_console = _build_console(
            resolved_env,
            resolved_background,
            surface_hex=resolved_background_hex,
            output_stream=sys.stdout if output_stream is None else output_stream,
        )
    else:
        from rich.console import Console

        injected_console = True
        resolved_console = console
        _normalize_injected_console_color(resolved_console, resolved_env)
        if isinstance(resolved_console, Console):
            resolved_console.push_theme(
                theme_for_background(resolved_background, surface_hex=resolved_background_hex)
            )
    width = _compute_width(
        resolved_env,
        resolved_console,
        force_width,
        injected_console=injected_console,
        explicit_env=env_was_provided,
    )
    _set_injected_console_width(resolved_console, width)
    mode = _compute_default_mode()
    limits = _DEFAULT_LIMITS
    if force_height is not None and force_height > 0:
        height_value = force_height
    elif force_height is not None:
        height_value = None
    else:
        size_obj: object = getattr(resolved_console, "size", None)
        height_attr: int | None = None
        if size_obj is not None:
            candidate: object = getattr(size_obj, "height", None)
            if isinstance(candidate, int) and candidate > 0:
                height_attr = candidate
        positive_height: int | None = height_attr
        height_value = positive_height

    color_enabled = not resolved_env.no_color and not _console_has_no_color(
        resolved_console,
        injected_console=injected_console,
    )

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
        theme=theme_for_background(resolved_background, surface_hex=resolved_background_hex),
        width=width,
        mode=mode,
        height=height_value,
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
        body_measure_cap=limits.body_measure_cap,
        terminal_background_is_light=resolved_background,
        terminal_background_hex=resolved_background_hex,
        env=env_dict,
        _resolved_env=resolved_env,
        _force_width=force_width,
        _force_glyphs=force_glyphs,
        _force_height=force_height,
        _explicit_env=env_was_provided,
    )


def install_sigwinch_refresher(
    ctx_holder: list[DisplayContext],
    on_refresh: Callable[[DisplayContext], None] | None = None,
    *,
    signal_getter: SignalGetter | None = None,
    signal_setter: SignalSetter | None = None,
) -> Callable[[], None]:
    if sys.platform == "win32":
        return lambda: None
    if threading.main_thread() is not threading.current_thread():
        return lambda: None

    getter = signal_getter if signal_getter is not None else cast("SignalGetter", signal.getsignal)
    setter = signal_setter if signal_setter is not None else cast("SignalSetter", signal.signal)
    previous = getter(signal.SIGWINCH)
    refresh_in_progress = False

    def handler(_signum: int, _frame: object) -> None:
        nonlocal refresh_in_progress
        if refresh_in_progress:
            return
        refresh_in_progress = True
        try:
            refreshed = ctx_holder[0].refreshed()
            ctx_holder[0] = refreshed
            if on_refresh is not None:
                on_refresh(refreshed)
        except Exception:
            return
        finally:
            refresh_in_progress = False

    setter(signal.SIGWINCH, handler)

    def stop() -> None:
        setter(signal.SIGWINCH, signal.SIG_DFL if previous is None else previous)

    return stop


def install_poll_refresher(
    ctx_holder: list[DisplayContext],
    interval_seconds: float = 2.0,
    on_refresh: Callable[[DisplayContext], None] | None = None,
) -> Callable[[], None]:
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
    *,
    signal_getter: SignalGetter | None = None,
    signal_setter: SignalSetter | None = None,
) -> Callable[[], None]:
    if sys.platform != "win32" and threading.main_thread() is threading.current_thread():
        return install_sigwinch_refresher(
            ctx_holder,
            on_refresh,
            signal_getter=signal_getter,
            signal_setter=signal_setter,
        )
    return install_poll_refresher(ctx_holder, interval_seconds=2.0, on_refresh=on_refresh)


__all__ = [
    "DisplayContext",
    "install_poll_refresher",
    "install_sigwinch_refresher",
    "install_width_refresher",
    "make_display_context",
]
