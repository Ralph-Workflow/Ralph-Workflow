"""Fixed-RGB Markdown styles for background-aware previews."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from rich.theme import Theme

if TYPE_CHECKING:
    from collections.abc import Iterator

    from rich.console import Console

_PALETTES: dict[bool | None, tuple[str, str, str, str, str, str]] = {
    False: ("#D0D0D0", "#6DDCF2", "#0CB9F2", "#C9D921", "#77D9B0", "#94D90B"),
    True: ("#202020", "#3C7F85", "#251947", "#70703E", "#3E4712", "#330B03"),
    None: ("#757575", "#408070", "#2070F0", "#608020", "#5070D0", "#7070A0"),
}


def _styles(terminal_bg_is_light: bool | None) -> dict[str, str]:
    default, accent, link, url, bullet, rule = _PALETTES[terminal_bg_is_light]
    return {
        "markdown.code": f"bold {default}",
        "markdown.em": f"italic {default}",
        "markdown.strong": f"bold {default}",
        "markdown.s": f"strike {default}",
        "markdown.code_block": default,
        "markdown.table.border": accent,
        "markdown.table.header": f"bold {default}",
        "markdown.block_quote": accent,
        "markdown.link": f"underline {link}",
        "markdown.link_url": url,
        **{f"markdown.h{level}": f"bold {accent}" for level in range(1, 7)},
        "markdown.item.bullet": bullet,
        "markdown.item.number": bullet,
        "markdown.list": bullet,
        "markdown.kbd": f"bold {accent}",
        "markdown.hr": rule,
    }


@contextmanager
def markdown_theme_context(
    console: Console, *, terminal_bg_is_light: bool | None
) -> Iterator[None]:
    """Apply contrast-safe Markdown styles while rendering one preview."""
    with console.use_theme(Theme(_styles(terminal_bg_is_light))):
        yield
