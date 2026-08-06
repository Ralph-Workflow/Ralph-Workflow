"""Monokai-derived Markdown styles for background-aware previews.

Each palette is solved per surface by :mod:`ralph.display._palette` rather
than read from a fixed table.
"""

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
        # Body text (default) is the near-neutral foreground, not a hue
        # accent -- matching Monokai Pro's own body colour and the S-4/S-5
        # repoint of the neutral display roles (PLAN.md).
        default = solve_for_surface(ROLE_ANCHORS["foreground"], preview_surface)
        accent = solve_for_surface(ROLE_ANCHORS["info"], preview_surface)
        link = solve_for_surface(ROLE_ANCHORS["info"], preview_surface)
        url = solve_for_surface(ROLE_ANCHORS["skipped"], preview_surface)
        bullet = solve_for_surface(ROLE_ANCHORS["success"], preview_surface)
        rule = solve_for_surface(ROLE_ANCHORS["success"], preview_surface)
    else:
        default = solve_dual_safe(ROLE_ANCHORS["foreground"])
        accent = solve_dual_safe(ROLE_ANCHORS["info"])
        link = solve_dual_safe(ROLE_ANCHORS["info"])
        url = solve_dual_safe(ROLE_ANCHORS["skipped"])
        bullet = solve_dual_safe(ROLE_ANCHORS["success"])
        rule = solve_dual_safe(ROLE_ANCHORS["success"])
    return default, accent, link, url, bullet, rule


_PALETTES: dict[bool | None, tuple[str, str, str, str, str, str]] = {
    # Single-sourced through the same canonical-surface derivation the
    # measured (surface_hex) path uses, via preview_background_for_background
    # (S-6) -- rather than the raw #101417/#F7F9FB literals that previously
    # disagreed with it.
    False: _build_markdown_palette(preview_background_for_background(False)),
    True: _build_markdown_palette(preview_background_for_background(True)),
    None: _build_markdown_palette(None),
}


def _markdown_palette_for_surface(surface_hex: str) -> tuple[str, str, str, str, str, str]:
    """Return the Markdown palette solved against the measured terminal surface."""
    return _markdown_palette_for_surface_cached(surface_hex)


def _markdown_palette_for_surface_uncached(
    surface_hex: str,
) -> tuple[str, str, str, str, str, str]:
    preview_surface = preview_background_for_background(None, surface_hex=surface_hex)
    return _build_markdown_palette(preview_surface)


# Call form (rather than decorator form) keeps mypy's disallow_any_explicit /
# disallow_any_decorated settings clean without a type: ignore suppression --
# the same first-party idiom used by ralph.display.language_inference._cached_infer.
_markdown_palette_for_surface_cached = lru_cache(maxsize=8)(
    _markdown_palette_for_surface_uncached
)


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
