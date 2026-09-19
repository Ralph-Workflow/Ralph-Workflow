"""Regression coverage for Rich's cross-Console style cache contamination."""

from __future__ import annotations

from collections.abc import Callable
from io import StringIO

from rich.color import Color
from rich.console import Console
from rich.style import Style

_RICH_STYLE_COLOR_CACHES: tuple[Callable[[], None], ...] = (
    Color.parse.cache_clear,
    Color.downgrade.cache_clear,
    Color.get_ansi_codes.cache_clear,
    Style.parse.cache_clear,
    Style.normalize.cache_clear,
    Style._add.cache_clear,
)


def _clear_rich_style_color_caches() -> None:
    for cache_clear in _RICH_STYLE_COLOR_CACHES:
        cache_clear()


def _render_hex_colour(color_system: str) -> str:
    stream = StringIO()
    Console(file=stream, force_terminal=True, color_system=color_system).print(
        "carrier",
        style=Style.parse("#ef8a62"),
    )
    return stream.getvalue()


def test_standard_console_cache_contaminates_later_truecolor_console() -> None:
    """A shared Rich style retains standard ANSI codes after a console-depth switch."""
    _clear_rich_style_color_caches()

    standard = _render_hex_colour("standard")
    stale_truecolor = _render_hex_colour("truecolor")

    assert "\x1b[38;2;" not in standard
    assert "\x1b[38;2;" not in stale_truecolor

    _clear_rich_style_color_caches()

    fresh_truecolor = _render_hex_colour("truecolor")
    assert "\x1b[38;2;239;138;98m" in fresh_truecolor


def test_style_parse_and_add_caches_are_the_only_required_resets() -> None:
    """Clearing the two caches that retain mutable styles restores truecolor output."""
    _clear_rich_style_color_caches()
    _render_hex_colour("standard")

    Style.parse.cache_clear()
    Style._add.cache_clear()

    fresh_truecolor = _render_hex_colour("truecolor")
    assert "\x1b[38;2;239;138;98m" in fresh_truecolor
