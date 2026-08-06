"""Fixed-RGB Markdown styles for background-aware previews."""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from typing import TYPE_CHECKING

from rich.theme import Theme

if TYPE_CHECKING:
    from collections.abc import Iterator

    from rich.console import Console

from ralph.display._palette import ROLE_ANCHORS, solve_dual_safe, solve_for_surface
from ralph.display.theme import preview_background_for_background


def _build_markdown_palette(preview_surface: str | None) -> tuple[str, str, str, str, str, str]:
    if preview_surface is not None:
        default = solve_for_surface(ROLE_ANCHORS["chrome"], preview_surface)
        accent = solve_for_surface(ROLE_ANCHORS["info"], preview_surface)
        link = solve_for_surface(ROLE_ANCHORS["info"], preview_surface)
        url = solve_for_surface(ROLE_ANCHORS["skipped"], preview_surface)
        bullet = solve_for_surface(ROLE_ANCHORS["success"], preview_surface)
        rule = solve_for_surface(ROLE_ANCHORS["success"], preview_surface)
    else:
        default = solve_dual_safe(ROLE_ANCHORS["chrome"])
        accent = solve_dual_safe(ROLE_ANCHORS["info"])
        link = solve_dual_safe(ROLE_ANCHORS["info"])
        url = solve_dual_safe(ROLE_ANCHORS["skipped"])
        bullet = solve_dual_safe(ROLE_ANCHORS["success"])
        rule = solve_dual_safe(ROLE_ANCHORS["success"])
    return default, accent, link, url, bullet, rule


_PALETTES: dict[bool | None, tuple[str, str, str, str, str, str]] = {
    False: _build_markdown_palette("#101417"),
    True: _build_markdown_palette("#F7F9FB"),
    None: _build_markdown_palette(None),
}


def _markdown_palette_for_surface(surface_hex: str) -> tuple[str, str, str, str, str, str]:
    """Return the Markdown palette solved against the measured terminal surface."""
    return _markdown_palette_for_surface_cached(surface_hex)


@lru_cache(maxsize=8)  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
def _markdown_palette_for_surface_cached(
    surface_hex: str,
) -> tuple[str, str, str, str, str, str]:
    preview_surface = preview_background_for_background(None, surface_hex=surface_hex)
    return _build_markdown_palette(preview_surface)


def _styles(terminal_bg_is_light: bool | None, surface_hex: str | None = None) -> dict[str, str]:
    default, accent, link, url, bullet, rule = (
        _markdown_palette_for_surface(surface_hex)
        if surface_hex is not None
        else _PALETTES[terminal_bg_is_light]
    )
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
    console: Console, *, terminal_bg_is_light: bool | None, surface_hex: str | None = None
) -> Iterator[None]:
    """Apply contrast-safe Markdown styles while rendering one preview."""
    with console.use_theme(Theme(_styles(terminal_bg_is_light, surface_hex=surface_hex))):
        yield
