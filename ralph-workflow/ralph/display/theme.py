"""Monokai Pro-derived theme helpers for Ralph CLI display.

Background-aware Rich and Pygments theme helpers built from a deterministic
palette generator (:mod:`ralph.display._palette`). Every semantic colour is
solved against the resolved terminal surface -- rather than read from a fixed
RGB table -- so contrast and hue identity are preserved on light, dark, and
unknown terminal backgrounds.

All semantic colors are selected from the resolved terminal background and
must preserve contrast and non-color structural carriers.

Semantic states are distinguished by hue, using OKLCh hue anchors seeded
from Monokai Pro's measured characteristics
(``ralph.display._palette.ROLE_ANCHORS``). ``STATUS_STYLES`` carries a
``(rich_style, unicode_icon, ascii_label)`` tuple per semantic state so
display code can retain every meaning carrier on colorblind and no-color
consoles. Semantic roles are defined once here; background-aware role
tables preserve contrast and hue identity on light, dark, and unknown
terminal backgrounds.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import TYPE_CHECKING, Final, Literal

from rich.console import Console
from rich.style import Style
from rich.syntax import PygmentsSyntaxTheme, SyntaxTheme
from rich.theme import Theme

import ralph.display._identity as identity_helpers
from ralph.display import _palette
from ralph.display._identity import (
    _DEUTERANOPIA_MATRIX,
    _PROTANOPIA_MATRIX,
    _TRITANOPIA_MATRIX,
)
from ralph.display._identity import (
    normalize_identity_name as _normalize_identity_name,
)
from ralph.display._palette import (
    _CANONICAL_DARK_SURFACE_HEX,
    _CANONICAL_LIGHT_SURFACE_HEX,
    _PURE_BLACK_HEX,
    _PURE_WHITE_HEX,
)
from ralph.syntax_theme import SyntaxThemes

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from typing import TextIO

UNICODE_GLYPHS: Final[dict[str, str]] = {
    "success": "\u2713",
    "error": "\u2717",
    "warning": "\u26a0",
    "running": "\u25d0",
    "pending": "\u25cb",
    "info": "i",
    "milestone": "\u25c6",
    "arrow": "\u2192",
    "start": "\u25b6",
    "phase_marker": "\u25a0",
    "iteration": "\u21bb",
    "budget": "\u25b2",
    "review_pass": "\u2714",
    "review_fail": "\u2718",
    "outer_dev": "\u25ce",
    "inner_analysis": "\u25b8",
    "proceed": "\u2191",
    "revise": "\u2193",
    "rule": "\u2500\u2500\u2500",
    "stalled": "\u26a0",
    "terminated": "\u25a0",
    "waiting": "\u25cb",
    "retrying": "\u21bb",
    "liveness": "\u280b",
}

ASCII_GLYPHS: Final[dict[str, str]] = {
    "success": "[OK]",
    "error": "[X]",
    "warning": "[!]",
    "running": "[*]",
    "pending": "[ ]",
    "info": "[i]",
    "milestone": "*",
    "arrow": "->",
    "start": ">",
    "phase_marker": "[]",
    "iteration": "~",
    "budget": "^",
    "review_pass": "[+]",
    "review_fail": "[-]",
    "outer_dev": "[OD]",
    "inner_analysis": "[IA]",
    "proceed": "^",
    "revise": "v",
    "rule": "---",
    "stalled": "[!]",
    "terminated": "[OK]",
    "waiting": "[ ]",
    "retrying": "[~]",
    "liveness": "*",
}

_RALPH_FORCE_ASCII_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on"})


def detect_glyph_capability(stream: object, env: Mapping[str, str]) -> bool:
    """Return False when glyphs should fall back to ASCII, True for Unicode.

    Heuristic order (highest to lowest precedence):
    1. RALPH_FORCE_ASCII env var (any truthy value) \u2192 ASCII
    2. stream.encoding exists and 'utf' not in encoding.lower() \u2192 ASCII
    3. TERM=dumb \u2192 ASCII
    4. Otherwise \u2192 Unicode
    """
    # Check explicit env override first
    force_ascii = env.get("RALPH_FORCE_ASCII", "").lower().strip()
    if force_ascii in _RALPH_FORCE_ASCII_TRUTHY:
        return False

    # Check stream encoding
    encoding: object = getattr(stream, "encoding", None)
    if encoding is not None:
        encoding_str = str(encoding).lower()
        if "utf" not in encoding_str:
            return False

    # Check TERM=dumb
    term = env.get("TERM", "")
    return term != "dumb"


#: Representative realistic terminal surfaces used to solve the
#: measurement-free canonical palettes -- the boolean-only dark/light
#: tables, the identity palette, and the ``RALPH_TERMINAL_BG=dark``/
#: ``light`` fallback used when no OSC 11 measurement is available
#: (see :func:`detect_terminal_background_hex`). Solving against the
#: pure endpoints (``#000000`` / ``#FFFFFF``) left zero contrast
#: headroom: a pigment solved to exactly 4.5:1 on ``#000000`` measures
#: only ~3.04:1 on a realistic ``#2D2A2E`` terminal (DA-001).
#: ``#2D2A2E`` (Y=0.0242) is the lightest -- hardest -- of the common
#: dark surfaces (``#1E1E1E`` Y=0.013 and ``#282C34`` Y=0.0238 are
#: both covered because they are darker still); ``#FAF8F5`` (Y=0.9405)
#: is the corresponding hardest common light surface, just under pure
#: white.
_pal_dark = _palette.resolve_palette(_CANONICAL_DARK_SURFACE_HEX)
_pal_light = _palette.resolve_palette(_CANONICAL_LIGHT_SURFACE_HEX)
_pal_unknown = _palette.resolve_palette(None)


def _build_status_styles(palette: Mapping[str, str]) -> dict[str, tuple[str, str, str]]:
    return {
        "success": (f"bold {palette['success']}", "\u2713", "PASS"),
        "running": (palette["running"], "\u25d0", "RUN"),
        "warning": (f"bold {palette['warning']}", "\u26a0", "WARN"),
        "error": (f"bold {palette['error']}", "\u2717", "FAIL"),
        "skipped": (palette["skipped"], "\u25cb", "SKIP"),
        "pending": (palette["pending"], "\u25cb", "WAIT"),
        "info": (palette["info"], "\u2139", "INFO"),
    }


STATUS_STYLES: Final[dict[str, tuple[str, str, str]]] = _build_status_styles(_pal_dark)
STATUS_STYLES_ON_LIGHT_BG: Final[dict[str, tuple[str, str, str]]] = _build_status_styles(_pal_light)
STATUS_STYLES_ON_UNKNOWN_BG: Final[dict[str, tuple[str, str, str]]] = _build_status_styles(_pal_unknown)


def _build_display_styles(palette: Mapping[str, str]) -> dict[str, str]:
    return {
        "chrome": palette["chrome"],
        "agent_text": palette["agent_text"],
        "elision": palette["elision"],
        "diff_added": palette["diff_added"],
        "diff_removed": palette["diff_removed"],
    }


_DISPLAY_STYLES: Final[dict[str, str]] = _build_display_styles(_pal_dark)
_DISPLAY_STYLES_ON_LIGHT_BG: Final[dict[str, str]] = _build_display_styles(_pal_light)
_DISPLAY_STYLES_ON_UNKNOWN_BG: Final[dict[str, str]] = _build_display_styles(_pal_unknown)


#: The six Monokai Pro accent roles the identity palette is seeded from.
_IDENTITY_ACCENT_ROLES: Final[tuple[str, ...]] = (
    "error",
    "warning",
    "skipped",
    "success",
    "info",
    "pending",
)


#: Two naive pairwise midpoints, at the two accents' averaged hue and/or
#: reference lightness, land in the same 6x6x6 colour-cube index as a
#: neighbouring anchor -- across all three of the dark, light, and
#: dual-safe (unknown) tables together, not any single one of them alone:
#: warning->skipped (hue ~68.3, L 0.834) collides with skipped itself
#: (L 0.894) on the dark and unknown tables, both pinning the red channel
#: at 255 in the saturated yellow-orange corner; skipped->success
#: (hue 110.6, L 0.865) collides with success (L 0.836) on the light table,
#: where mirroring the large skipped offset pushes both into the same
#: too-dark corner. Pulling warning->skipped further toward warning (both
#: hue and lightness) and skipped->success down to success's own reference
#: lightness clears every collision on all three tables and all three CVD
#: matrices at once -- verified directly against
#: ``solve_for_surface``/``solve_dual_safe``. This is the tuning PLAN.md S-7
#: anticipates budgeting for -- equal-spaced midpoints alone are measured to
#: be insufficient.
_IDENTITY_HUE_OVERRIDES: Final[dict[tuple[str, str], float]] = {
    ("warning", "skipped"): 55.0,
    # A-6 re-tune: once solve_for_surface re-solves chroma per surface
    # instead of holding it fixed (PLAN.md S-2), the naive success->info
    # midpoint (hue ~168.2) collides with `success` itself in the
    # 256-colour cube on the light table. Lifting the midpoint's l_ref
    # toward info's own (below) moves it decisively into a distinct cube
    # cell on all three tables and all three CVD matrices at once --
    # verified directly against solve_for_surface/solve_dual_safe.
    ("success", "info"): 180.0,
}
_IDENTITY_L_REF_OVERRIDES: Final[dict[tuple[str, str], float]] = {
    ("warning", "skipped"): 0.72,
    ("skipped", "success"): 0.8361,
    ("success", "info"): 0.89,
}


def _named_anchor_hue(pair: tuple[str, _palette.RoleAnchor]) -> float:
    """Sort key: order a (role, anchor) pair by the anchor's hue."""
    return pair[1].hue


def _identity_seed_anchors() -> tuple[_palette.RoleAnchor, ...]:
    """Build twelve identity slots from the six Monokai Pro accent anchors:
    the six anchors themselves, plus their six pairwise midpoints -- hue,
    chroma AND reference lightness together, not hue/chroma alone -- closing
    the full hue wheel including the wrap segment from ``pending`` back
    around to ``error``. Carrying reference lightness through the
    interpolation is what gives the identity palette the accents' own
    lightness spread (L 0.706-0.894) instead of the flat single-plane
    chroma-only interpolation that left neighbouring slots colliding after
    256-colour quantisation (see PLAN.md S-7's Characterize measurements).
    """
    named: list[tuple[str, _palette.RoleAnchor]] = [
        (role, _palette.ROLE_ANCHORS[role]) for role in _IDENTITY_ACCENT_ROLES
    ]
    named.sort(key=_named_anchor_hue)
    count = len(named)
    slots: list[_palette.RoleAnchor] = []
    for index in range(count):
        first_role, first = named[index]
        second_role, second = named[(index + 1) % count]
        # Unwrap the wrap segment (pending -> error) past 360 degrees so the
        # midpoint lands between them rather than on the short way around.
        second_hue = second.hue + 360.0 if second.hue <= first.hue else second.hue
        midpoint_hue = _IDENTITY_HUE_OVERRIDES.get(
            (first_role, second_role), (first.hue + second_hue) / 2.0
        )
        midpoint_l_ref = _IDENTITY_L_REF_OVERRIDES.get(
            (first_role, second_role), (first.l_ref + second.l_ref) / 2.0
        )
        slots.append(first)
        slots.append(
            _palette.RoleAnchor(
                hue=midpoint_hue % 360.0,
                chroma=(first.chroma + second.chroma) / 2.0,
                l_ref=midpoint_l_ref,
            )
        )
    return tuple(slots)


_IDENTITY_SEED_ANCHORS: Final[tuple[_palette.RoleAnchor, ...]] = _identity_seed_anchors()


def _generate_identity_palette(surface_hex: str | None) -> tuple[str, ...]:
    slots: list[str] = []
    for anchor in _IDENTITY_SEED_ANCHORS:
        if surface_hex is not None:
            hex_val = _palette.solve_for_surface(anchor, surface_hex)
        else:
            hex_val = _palette.solve_dual_safe(anchor)
        slots.append(hex_val)
    return tuple(slots)


IDENTITY_PALETTE: Final[tuple[str, ...]] = _generate_identity_palette(_CANONICAL_DARK_SURFACE_HEX)
IDENTITY_PALETTE_ON_LIGHT_BG: Final[tuple[str, ...]] = _generate_identity_palette(
    _CANONICAL_LIGHT_SURFACE_HEX
)
IDENTITY_PALETTE_ON_UNKNOWN_BG: Final[tuple[str, ...]] = _generate_identity_palette(None)

# The shipped roster is the baseline active set for shared surfaces. Rendering
# it collision-aware prevents a hash collision before a particular display
# has observed every peer in the current run.
_DISPLAY_IDENTITY_ACTIVE_SET: Final[tuple[str, ...]] = (
    "claude",
    "claude-headless",
    "codex",
    "opencode",
    "nanocoder",
    "agy",
    "pi",
    "cursor",
)


def _simulate_cvd(
    hex_color: str,
    matrix: tuple[tuple[float, float, float], ...],
) -> str:
    """Return the documented color-vision-deficiency simulation for a hex color."""
    return identity_helpers.simulate_cvd(hex_color, matrix, rgb=_rgb)


def _identity_slot(name: str) -> int:
    """Return the deterministic palette slot for ``name``."""
    return identity_helpers.identity_slot(name, len(IDENTITY_PALETTE))


def _status_role_hexes() -> frozenset[str]:
    hexes: set[str] = set()
    for table in (STATUS_STYLES, STATUS_STYLES_ON_LIGHT_BG, STATUS_STYLES_ON_UNKNOWN_BG):
        for style, _icon, _label in table.values():
            extracted = _extract_hex(style)
            if extracted:
                hexes.add(extracted.lower())
    return frozenset(hexes)


def _hex_distance(a: str, b: str) -> float:
    ax, ay, az = _rgb(a)
    bx, by, bz = _rgb(b)
    return math.sqrt(float((ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2))


def _rgb(hex_color: str) -> tuple[int, int, int]:
    body = hex_color.lstrip("#")
    if len(body) == _HEX_SHORT_LEN:
        body = "".join(ch * 2 for ch in body)
    if len(body) != _HEX_LONG_LEN:
        raise ValueError(f"expected #RRGGBB or #RGB hex, got {hex_color!r}")
    return (int(body[0:2], 16), int(body[2:4], 16), int(body[4:6], 16))


def identity_color(
    name: str,
    *,
    active: Iterable[str] | None = None,
    terminal_bg_is_light: bool | None,
    surface_hex: str | None = None,
) -> str:
    """Return the hex color for an identity, with collision-nudge."""
    if surface_hex is not None:
        palette = _generate_identity_palette(surface_hex)
    elif terminal_bg_is_light is True:
        palette = IDENTITY_PALETTE_ON_LIGHT_BG
    elif terminal_bg_is_light is False:
        palette = IDENTITY_PALETTE
    else:
        palette = IDENTITY_PALETTE_ON_UNKNOWN_BG
    return identity_helpers.identity_color(name, palette=palette, active=active, rgb=_rgb)


def _build_theme_styles(palette: Mapping[str, str]) -> dict[str, str]:
    disp = _build_display_styles(palette)
    return {
        "theme.level.info": disp["agent_text"],
        "theme.level.warn": f"bold {palette['warning']}",
        "theme.level.success": f"bold {palette['success']}",
        "theme.level.error": f"bold {palette['error']}",
        # E-2: milestone is a structural chrome cue (a rare beat marker),
        # not the semantic info state -- repointed to disp["chrome"] along
        # with the rest of this block's structural roles below.
        "theme.level.milestone": f"bold {disp['chrome']}",
        "theme.cat.meta": disp["chrome"],
        "theme.cat.cont": disp["agent_text"],
        "theme.cat.out": disp["agent_text"],
        "theme.log.error": f"bold {palette['error']}",
        "theme.log.info": palette["info"],
        "theme.log.milestone": f"bold {disp['chrome']}",
        "theme.log.success": f"bold {palette['success']}",
        "theme.log.warn": f"bold {palette['warning']}",
        # E-2: panel chrome, banner chrome, phase labels below, text emphasis,
        # and outer_dev are all structural (tier 2) -- they previously shared
        # `info`'s full tier-3 chroma, which made the single most-rendered
        # accent on screen also mean "the info status". Repointed to
        # disp["chrome"] (a distinct, lower-chroma pigment on the same hue --
        # see _palette.ROLE_ANCHORS["chrome"]'s docstring). The semantic
        # `info` STATE (theme.status.info / theme.log.info / STATUS_STYLES)
        # keeps palette["info"]'s full chroma unchanged.
        "theme.panel.border": disp["chrome"],
        "theme.panel.title": f"bold {disp['chrome']}",
        "theme.phase.commit": disp["chrome"],
        "theme.phase.complete": f"bold {palette['success']}",
        "theme.phase.development": palette["success"],
        "theme.phase.development_analysis": palette["analysis"],
        "theme.phase.development_commit": disp["chrome"],
        "theme.phase.failed": f"bold {palette['error']}",
        "theme.phase.fix": palette["error"],
        "theme.phase.planning": disp["chrome"],
        "theme.phase.review": palette["warning"],
        "theme.phase.review_analysis": palette["analysis"],
        "theme.phase.review_commit": disp["chrome"],
        "theme.status.error": f"bold {palette['error']}",
        "theme.status.failure": f"bold {palette['error']}",
        "theme.status.info": palette["info"],
        "theme.status.pending": palette["pending"],
        "theme.status.running": palette["running"],
        "theme.status.skipped": palette["skipped"],
        "theme.status.success": f"bold {palette['success']}",
        "theme.status.warning": f"bold {palette['warning']}",
        "theme.text.dim_italic": f"italic {palette['analysis']}",
        "theme.text.emphasis": f"bold {disp['chrome']}",
        "theme.text.muted": palette["muted"],
        "theme.banner.ascii": f"bold {disp['chrome']}",
        "theme.banner.border": disp["chrome"],
        "theme.banner.tagline": palette["muted"],
        "theme.banner.title": f"bold {disp['chrome']}",
        "theme.banner.version": f"bold {palette['success']}",
        "theme.banner.welcome": f"bold {disp['chrome']}",
        "theme.outer_dev": f"bold {disp['chrome']}",
        "theme.inner_analysis": palette["analysis"],
        "theme.review_pass": f"bold {palette['success']}",
        "theme.review_fail": f"bold {palette['error']}",
        "theme.proceed": f"bold {palette['success']}",
        "theme.revise": f"bold {palette['warning']}",
        "theme.status.bar_marker": palette["muted"],
        "theme.status.path_marker": palette["muted"],
        "theme.status.path": palette["muted"],
        "theme.display.chrome": disp["chrome"],
        "theme.display.agent_text": disp["agent_text"],
        "theme.display.elision": disp["elision"],
        "theme.diff.added": disp["diff_added"],
        "theme.diff.removed": disp["diff_removed"],
    }


_THEME_STYLES: Final[dict[str, str]] = _build_theme_styles(_pal_dark)
_THEME_STYLES_ON_LIGHT_BG: Final[dict[str, str]] = _build_theme_styles(_pal_light)
_THEME_STYLES_ON_UNKNOWN_BG: Final[dict[str, str]] = _build_theme_styles(_pal_unknown)

RALPH_THEME: Final[Theme] = Theme(_THEME_STYLES)
RALPH_THEME_ON_LIGHT_BG: Final[Theme] = Theme(_THEME_STYLES_ON_LIGHT_BG)
RALPH_THEME_ON_UNKNOWN_BG: Final[Theme] = Theme(_THEME_STYLES_ON_UNKNOWN_BG)

def _fresh_style(style: str) -> Style:
    """Build one style whose mutable ANSI cache cannot cross consoles."""
    tokens = style.split()
    background_index = tokens.index("on") + 1 if "on" in tokens else None
    fresh = Style(
        color=next((token for index, token in enumerate(tokens) if token.startswith("#") and index != background_index), None), bgcolor=tokens[background_index] if background_index is not None else None,
        bold=True if "bold" in tokens else None, dim=True if "dim" in tokens else None, italic=True if "italic" in tokens else None, underline=True if "underline" in tokens else None, reverse=True if "reverse" in tokens else None, strike=True if "strike" in tokens else None,
    )
    # Per-instance hash isolates Rich's ANSI cache; equality differs, so adjacent segments may not merge.
    fresh._hash = id(fresh)
    return fresh

def _fresh_theme(styles: Mapping[str, str]) -> Theme:
    return Theme({name: _fresh_style(style) for name, style in styles.items()})


def theme_for_background(
    terminal_bg_is_light: bool | None, surface_hex: str | None = None
) -> Theme:
    """Return a new theme so ANSI caching cannot downgrade another console."""
    if surface_hex is not None:
        return _fresh_theme(_build_theme_styles(_palette.resolve_palette(surface_hex)))
    if terminal_bg_is_light is True:
        return _fresh_theme(_THEME_STYLES_ON_LIGHT_BG)
    if terminal_bg_is_light is False:
        return _fresh_theme(_THEME_STYLES)
    return _fresh_theme(_THEME_STYLES_ON_UNKNOWN_BG)

_MIN_CONTRAST_RATIO: Final[float] = 4.5


_SRGB_LOW_CUTOFF: Final[float] = 0.03928
_SRGB_LINEAR_DIVISOR: Final[float] = 12.92
_SRGB_GAMMA_OFFSET: Final[float] = 0.055
_SRGB_GAMMA_SCALE: Final[float] = 1.055
_SRGB_GAMMA_EXPONENT: Final[float] = 2.4


_HEX_SHORT_LEN: Final[int] = 3
_HEX_LONG_LEN: Final[int] = 6
_COLORFGBG_MIN_PARTS: Final[int] = 2
_HEX_SHORT_TOKEN_LEN: Final[int] = _HEX_SHORT_LEN + 1
_HEX_LONG_TOKEN_LEN: Final[int] = _HEX_LONG_LEN + 1


def _srgb_channel_to_linear(value: float) -> float:
    if value <= _SRGB_LOW_CUTOFF:
        return value / _SRGB_LINEAR_DIVISOR
    base: float = (value + _SRGB_GAMMA_OFFSET) / _SRGB_GAMMA_SCALE
    exponent: float = _SRGB_GAMMA_EXPONENT
    result: float = base**exponent
    return result


def relative_luminance(hex_color: str) -> float:
    """Compute the WCAG relative luminance of a hex color.

    Accepts ``#rgb`` or ``#rrggbb`` hex strings and returns the
    sRGB-gamma-corrected luminance in [0, 1]. Used by the contrast
    and palette helpers to verify that status styles remain
    readable on dark and light terminal backgrounds.

    Parameters:
        hex_color: Color string in ``#rgb`` or ``#rrggbb`` form.

    Returns:
        The relative luminance as a float in [0, 1].
    """
    raw = hex_color.strip()
    if not raw.startswith("#"):
        raise ValueError(f"expected hex color starting with '#', got {hex_color!r}")
    body = raw[1:]
    if len(body) == _HEX_SHORT_LEN:
        body = "".join(ch * 2 for ch in body)
    if len(body) != _HEX_LONG_LEN or any(ch not in "0123456789abcdefABCDEF" for ch in body):
        raise ValueError(f"invalid hex color {hex_color!r}")
    red: float = int(body[0:2], 16) / 255.0
    green: float = int(body[2:4], 16) / 255.0
    blue: float = int(body[4:6], 16) / 255.0
    return (
        0.2126 * _srgb_channel_to_linear(red)
        + 0.7152 * _srgb_channel_to_linear(green)
        + 0.0722 * _srgb_channel_to_linear(blue)
    )


def contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    """Compute the WCAG contrast ratio between two hex colors.

    The ratio is ``(lighter + 0.05) / (darker + 0.05)`` where
    ``lighter`` and ``darker`` are the relative luminances of the
    two colors. A value of 1.0 means no contrast; 21.0 is the
    theoretical maximum. The display subsystem targets >= 4.5
    for body text and >= 3.0 for large text.

    Parameters:
        fg_hex: Foreground color hex string.
        bg_hex: Background color hex string.

    Returns:
        The contrast ratio as a float >= 1.0.
    """
    fg = relative_luminance(fg_hex)
    bg = relative_luminance(bg_hex)
    lighter, darker = (fg, bg) if fg >= bg else (bg, fg)
    return (lighter + 0.05) / (darker + 0.05)


def _extract_hex(style: str) -> str:
    for raw_token in style.split():
        token = raw_token.strip(",;").strip()
        if token.startswith("#") and len(token) in (_HEX_SHORT_LEN + 1, _HEX_LONG_LEN + 1):
            return token
    return ""


# DoD #6: single-sourced from _palette._PURE_BLACK_HEX/_PURE_WHITE_HEX --
# the same two literals solve_dual_safe checks against -- rather than a
# second, independent "#000000"/"#FFFFFF" declaration.
_DARK_BG_HEX: Final[str] = _PURE_BLACK_HEX
_LIGHT_BG_HEX: Final[str] = _PURE_WHITE_HEX


#: WCAG crossover luminance. Below this value light foregrounds
#: out-contrast dark ones against the background; above it, dark
#: foregrounds win. Derived by solving
#: ``(L + 0.05) / 0.05 == 1.05 / (L + 0.05)`` -- the exact luminance at
#: which the preference flips. Using the crossover rather than a
#: hand-picked "is it brighter than grey" threshold means an arbitrary
#: background colour (a warm cream, a deep teal, a mid-tone slate) is
#: classified by measured contrast, not by assumption.
_LIGHT_BG_LUMINANCE_CROSSOVER: Final[float] = 0.1791


def _explicit_background_override(env: Mapping[str, str]) -> bool | None:
    explicit = env.get("RALPH_TERMINAL_BG", "").lower().strip()
    if explicit in {"light", "1", "true", "yes"}:
        return True
    if explicit in {"dark", "0", "false", "no"}:
        return False
    return background_hex_is_light(explicit) if explicit.startswith("#") else None


def _colorfgbg_is_light(env: Mapping[str, str]) -> bool | None:
    parts = env.get("COLORFGBG", "").strip().split(";")
    if len(parts) < _COLORFGBG_MIN_PARTS or not parts[1].isdigit():
        return None
    return {7: True, 15: True, 0: False, 8: False}.get(int(parts[1]))


def background_hex_is_light(bg_hex: str) -> bool | None:
    """Classify a measured terminal background by WCAG luminance."""
    try:
        return relative_luminance(bg_hex) > _LIGHT_BG_LUMINANCE_CROSSOVER
    except ValueError:
        return None


def terminal_background_is_light(
    env: Mapping[str, str], *, measured_bg_hex: str | None = None
) -> bool | None:
    """Resolve measured OSC 11 colour, then override, then the COLORFGBG hint.

    B-1: the measured background wins. A successful probe outranks an
    explicit ``RALPH_TERMINAL_BG`` override -- the override is a fallback
    for terminals that cannot be measured, not a veto over a real
    measurement.
    """
    if measured_bg_hex:
        measured = background_hex_is_light(measured_bg_hex)
        if measured is not None:
            return measured
    explicit = _explicit_background_override(env)
    if explicit is not None:
        return explicit
    return _colorfgbg_is_light(env)


def _terminal_background_timeout_seconds(env: Mapping[str, str]) -> float:
    """Return the bounded OSC 11 deadline, accepting a positive ms override."""
    try:
        milliseconds = int(env.get("RALPH_TERMINAL_BG_TIMEOUT_MS", "100"))
    except ValueError:
        return 0.1
    return milliseconds / 1_000 if milliseconds > 0 else 0.1


def detect_terminal_background_is_light(env: Mapping[str, str]) -> bool | None:
    """Resolve the terminal background, OSC 11 first, then any override.

    B-1: the probe always runs first -- a successful measurement wins even
    over an explicit ``RALPH_TERMINAL_BG`` override. The override (or a
    malformed one) is only consulted once the probe fails to produce a
    usable measurement, the same fallback tier
    :func:`detect_terminal_background_hex` uses. The probe result is
    cached for the process lifetime (see ``_terminal_bg_query``), so
    running it unconditionally does not add a second measurement cost.
    """
    from ralph.display._terminal_bg_query import query_terminal_background_hex

    return terminal_background_is_light(
        env,
        measured_bg_hex=query_terminal_background_hex(
            timeout=_terminal_background_timeout_seconds(env)
        ),
    )


def detect_terminal_background_hex(env: Mapping[str, str]) -> str | None:
    """Resolve the measured or explicitly declared terminal background surface hex.

    B-1: the OSC 11 probe runs first -- a successful measurement wins over
    an explicit ``RALPH_TERMINAL_BG`` override. The probe result is cached
    for the process lifetime (see ``_terminal_bg_query``), so trying it
    before consulting the override does not add a second measurement cost.

    Only when the probe cannot measure the surface (``None``) does the
    override become the surface hex. A ``RALPH_TERMINAL_BG`` hex is only
    trusted when it is a valid ``#RGB`` / ``#RRGGBB`` colour
    (``relative_luminance`` already raises ``ValueError`` for malformed or
    wrong-length bodies) -- a malformed override falls through to the
    dual-safe fallback rather than being threaded, unvalidated, into the
    palette solver.

    A non-hex explicit declaration (``light`` / ``dark`` / ``1`` / ``true`` /
    ``yes`` / ``0`` / ``false`` / ``no``) resolves to the same representative
    surface the measurement-free canonical tables are solved against
    (``_CANONICAL_LIGHT_SURFACE_HEX`` / ``_CANONICAL_DARK_SURFACE_HEX``,
    i.e. ``#FAF8F5`` / ``#2D2A2E``) so this function and
    :func:`terminal_background_is_light` can never disagree about a
    declared background, and this declared-but-unmeasured path keeps the
    same contrast headroom DA-001 gave the canonical tables rather than
    resolving fresh against a pure endpoint with none.
    """
    from ralph.display._terminal_bg_query import query_terminal_background_hex

    measured = query_terminal_background_hex(
        timeout=_terminal_background_timeout_seconds(env)
    )
    if measured is not None:
        return measured

    explicit = env.get("RALPH_TERMINAL_BG", "").strip()
    if explicit.startswith("#"):
        try:
            relative_luminance(explicit)
        except ValueError:
            pass
        else:
            return explicit
    elif explicit:
        override = _explicit_background_override(env)
        if override is not None:
            return _CANONICAL_LIGHT_SURFACE_HEX if override else _CANONICAL_DARK_SURFACE_HEX
    return None


#: Monokai-derived syntax palettes solved for each terminal background.
SYNTAX_THEME_ON_DARK_BG: Final[SyntaxTheme] = PygmentsSyntaxTheme(SyntaxThemes.dark())
SYNTAX_THEME_ON_LIGHT_BG: Final[SyntaxTheme] = PygmentsSyntaxTheme(SyntaxThemes.light())
SYNTAX_THEME_ON_UNKNOWN_BG: Final[SyntaxTheme] = PygmentsSyntaxTheme(SyntaxThemes.unknown())

#: Rich's transparent syntax-background sentinel.
SYNTAX_BACKGROUND_TRANSPARENT: Final[str] = "default"

# Known backgrounds own previews; unknown terminals stay transparent. Both
# single-source through _palette.derive_preview_background against the same
# canonical surfaces the measured path (preview_background_for_background's
# surface_hex branch) uses, so the boolean and measured paths can no longer
# disagree (S-6 / DA-002).
_PREVIEW_BACKGROUND_ON_DARK_BG: Final[str] = _palette.derive_preview_background(
    _CANONICAL_DARK_SURFACE_HEX
)
_PREVIEW_BACKGROUND_ON_LIGHT_BG: Final[str] = _palette.derive_preview_background(
    _CANONICAL_LIGHT_SURFACE_HEX
)


def _derive_diff_fills(surface_hex: str) -> tuple[str, str]:
    """Derive removed/added diff fills from the single-sourced preview fill."""
    preview_hex = _palette.derive_preview_background(surface_hex)
    lab_l, a, b_lab = _palette.rgb_to_oklab(*_palette.hex_to_rgb(preview_hex))
    rem_r, rem_g, rem_b = _palette.oklab_to_rgb(lab_l, a + 0.012, b_lab)
    add_r, add_g, add_b = _palette.oklab_to_rgb(lab_l, a - 0.012, b_lab + 0.008)
    return _palette.rgb_to_hex(rem_r, rem_g, rem_b), _palette.rgb_to_hex(add_r, add_g, add_b)


def _preview_foreground_for_surface_uncached(surface_hex: str) -> str:
    fill = _palette.derive_preview_background(surface_hex)
    return _palette.solve_for_surface(_palette.ROLE_ANCHORS["chrome"], fill)


# Call form (rather than decorator form) keeps mypy's disallow_any_explicit /
# disallow_any_decorated settings clean without a type: ignore suppression, and
# resolves a per-surface foreground once instead of on every rendered row --
# the same first-party idiom used by ralph.display.language_inference._cached_infer.
_preview_foreground_for_surface_cached = lru_cache(maxsize=8)(
    _preview_foreground_for_surface_uncached
)

# Boolean and unknown-background preview foregrounds single-source through
# the same measured resolvers as surface_hex callers (S-6).
_PREVIEW_FOREGROUND_ON_DARK_BG: Final[str] = _preview_foreground_for_surface_cached(
    _CANONICAL_DARK_SURFACE_HEX
)
_PREVIEW_FOREGROUND_ON_LIGHT_BG: Final[str] = _preview_foreground_for_surface_cached(
    _CANONICAL_LIGHT_SURFACE_HEX
)
_PREVIEW_FOREGROUND_ON_UNKNOWN_BG: Final[str] = _palette.solve_dual_safe(
    _palette.ROLE_ANCHORS["chrome"]
)


def preview_foreground_for_background(
    terminal_bg_is_light: bool | None, surface_hex: str | None = None
) -> str:
    """Return the body foreground for an owned or transparent preview.

    Solved against the actual owned preview fill (not the raw terminal
    surface) so the result clears contrast on the surface it is really
    painted on.
    """
    if surface_hex is not None:
        return _preview_foreground_for_surface_cached(surface_hex)
    if terminal_bg_is_light is True:
        return _PREVIEW_FOREGROUND_ON_LIGHT_BG
    if terminal_bg_is_light is False:
        return _PREVIEW_FOREGROUND_ON_DARK_BG
    return _PREVIEW_FOREGROUND_ON_UNKNOWN_BG


def preview_background_for_background(
    terminal_bg_is_light: bool | None, surface_hex: str | None = None
) -> str:
    """Return the complete owned preview surface for a resolved background."""
    if surface_hex is not None:
        return _palette.derive_preview_background(surface_hex)
    if terminal_bg_is_light is True:
        return _PREVIEW_BACKGROUND_ON_LIGHT_BG
    if terminal_bg_is_light is False:
        return _PREVIEW_BACKGROUND_ON_DARK_BG
    return SYNTAX_BACKGROUND_TRANSPARENT


# Unknown-background diffs remain transparent. The known-background fills
# single-source through _derive_diff_fills against the same canonical
# surfaces the measured path uses (S-6).
_DIFF_REMOVED_FILL_ON_DARK_BG, _DIFF_ADDED_FILL_ON_DARK_BG = _derive_diff_fills(
    _CANONICAL_DARK_SURFACE_HEX
)
_DIFF_REMOVED_FILL_ON_LIGHT_BG, _DIFF_ADDED_FILL_ON_LIGHT_BG = _derive_diff_fills(
    _CANONICAL_LIGHT_SURFACE_HEX
)


def display_styles_for_background(
    terminal_bg_is_light: bool | None, surface_hex: str | None = None
) -> Mapping[str, str]:
    """Return meaning pigments for named console categories."""
    if surface_hex is not None:
        return _build_display_styles(_palette.resolve_palette(surface_hex))
    if terminal_bg_is_light is True:
        return _DISPLAY_STYLES_ON_LIGHT_BG
    if terminal_bg_is_light is False:
        return _DISPLAY_STYLES
    return _DISPLAY_STYLES_ON_UNKNOWN_BG


def _diff_token_foregrounds_for_surface_uncached(surface_hex: str) -> tuple[str, str]:
    removed_fill, added_fill = _derive_diff_fills(surface_hex)
    removed = _palette.solve_for_surface(_palette.ROLE_ANCHORS["diff_removed"], removed_fill)
    added = _palette.solve_for_surface(_palette.ROLE_ANCHORS["diff_added"], added_fill)
    return removed, added


# Call form (rather than decorator form) keeps mypy's disallow_any_explicit /
# disallow_any_decorated settings clean without a type: ignore suppression, and
# resolves a per-surface diff palette once instead of on every rendered row --
# the same first-party idiom used by ralph.display.language_inference._cached_infer.
_diff_token_foregrounds_for_surface_cached = lru_cache(maxsize=8)(
    _diff_token_foregrounds_for_surface_uncached
)


def diff_token_foregrounds(
    terminal_bg_is_light: bool | None, surface_hex: str | None = None
) -> tuple[str, str]:
    """Return deleted and inserted foregrounds distinct from failure/success states.

    On a known background (canonical boolean or measured ``surface_hex``)
    the markers are painted on their owned diff fill
    (:func:`diff_fill_styles`), so each foreground is solved against that
    fill rather than against the raw terminal surface. On an unknown
    background there is no owned fill -- the markers fall back to the
    dual-safe display-style pigments.
    """
    if surface_hex is not None:
        return _diff_token_foregrounds_for_surface_cached(surface_hex)
    fills = diff_fill_styles(terminal_bg_is_light)
    if fills is None:
        styles = display_styles_for_background(terminal_bg_is_light)
        return styles["diff_removed"], styles["diff_added"]
    removed_fill, added_fill = fills
    removed = _palette.solve_for_surface(_palette.ROLE_ANCHORS["diff_removed"], removed_fill)
    added = _palette.solve_for_surface(_palette.ROLE_ANCHORS["diff_added"], added_fill)
    return removed, added


def diff_fill_styles(
    terminal_bg_is_light: bool | None, surface_hex: str | None = None
) -> tuple[str, str] | None:
    """Return derived removed/added diff fills, or transparent for unknown backgrounds."""
    if surface_hex is not None:
        return _derive_diff_fills(surface_hex)
    if terminal_bg_is_light is True:
        return _DIFF_REMOVED_FILL_ON_LIGHT_BG, _DIFF_ADDED_FILL_ON_LIGHT_BG
    if terminal_bg_is_light is False:
        return _DIFF_REMOVED_FILL_ON_DARK_BG, _DIFF_ADDED_FILL_ON_DARK_BG
    return None


def _syntax_theme_for_surface_uncached(surface_hex: str) -> SyntaxTheme:
    return PygmentsSyntaxTheme(SyntaxThemes.for_surface(surface_hex))


# Call form (rather than decorator form) keeps mypy's disallow_any_explicit /
# disallow_any_decorated settings clean without a type: ignore suppression, and
# resolves a per-surface syntax theme once instead of on every rendered row --
# the same first-party idiom used by ralph.display.language_inference._cached_infer.
_syntax_theme_for_surface_cached = lru_cache(maxsize=8)(_syntax_theme_for_surface_uncached)


def syntax_theme_for_background(
    terminal_bg_is_light: bool | None, surface_hex: str | None = None
) -> SyntaxTheme:
    """Return the syntax theme resolved for this background."""
    if surface_hex is not None:
        return _syntax_theme_for_surface_cached(surface_hex)
    if terminal_bg_is_light is True:
        return SYNTAX_THEME_ON_LIGHT_BG
    if terminal_bg_is_light is False:
        return SYNTAX_THEME_ON_DARK_BG
    return SYNTAX_THEME_ON_UNKNOWN_BG


def pick_status_styles(
    terminal_bg_is_light: bool | None, surface_hex: str | None = None
) -> dict[str, tuple[str, str, str]]:
    """Return the status style table for the given background."""
    if surface_hex is not None:
        return _build_status_styles(_palette.resolve_palette(surface_hex))
    if terminal_bg_is_light is True:
        return STATUS_STYLES_ON_LIGHT_BG
    if terminal_bg_is_light is False:
        return STATUS_STYLES
    return STATUS_STYLES_ON_UNKNOWN_BG


def _state_payload_for_background(
    state: str,
    *,
    terminal_bg_is_light: bool | None,
) -> tuple[str, str, str]:
    table = pick_status_styles(terminal_bg_is_light)
    try:
        payload = table[state]
    except KeyError as exc:
        known = ", ".join(sorted(table))
        raise KeyError(f"Unknown status {state!r}. Known statuses: {known}") from exc
    return payload


def status_styles_for_context(
    terminal_bg_is_light: bool | None,
) -> dict[str, tuple[str, str, str]]:
    """Return the status style table for the current background.

    Convenience wrapper around :func:`pick_status_styles` that
    names the operation by intent ("styles for the current
    context") rather than by parameter. Used by callers that
    want the resolved table without choosing between the light
    and dark variants.

    Parameters:
        terminal_bg_is_light: ``True`` for light backgrounds,
            ``False`` for dark, ``None`` for unknown.

    Returns:
        A mapping from status name to ``(label, glyph, style)``.
    """
    return pick_status_styles(terminal_bg_is_light)


def assert_status_styles_meet_contrast(
    *,
    terminal_bg_is_light: bool | None,
    min_ratio: float = _MIN_CONTRAST_RATIO,
) -> None:
    """Assert that every status style meets the contrast minimum.

    Iterates the resolved status style table and verifies that
    the foreground hex color (extracted from the Rich style
    string) reaches at least ``min_ratio`` against the implied
    background color. Raises ``RuntimeError`` listing every
    failure when any style fails the test.

    Parameters:
        terminal_bg_is_light: ``True`` for light backgrounds,
            ``False`` for dark, ``None`` for unknown (checks both).
        min_ratio: Minimum acceptable contrast ratio (default
            ``_MIN_CONTRAST_RATIO``).

    Raises:
        RuntimeError: When one or more status styles fail the
            contrast check.
    """
    table = pick_status_styles(terminal_bg_is_light)
    backgrounds = (
        (_LIGHT_BG_HEX,)
        if terminal_bg_is_light is True
        else ((_DARK_BG_HEX,) if terminal_bg_is_light is False else (_DARK_BG_HEX, _LIGHT_BG_HEX))
    )
    failures: list[str] = []
    for state, payload in table.items():
        style = payload[0]
        fg_hex = _extract_hex(style)
        if not fg_hex:
            continue
        for bg_hex in backgrounds:
            ratio = contrast_ratio(fg_hex, bg_hex)
            if ratio < min_ratio:
                failures.append(f"  {state}: {fg_hex} on {bg_hex} = {ratio:.2f}:1 (< {min_ratio})")
    if failures:
        joined = "\n".join(failures)
        raise RuntimeError(
            "STATUS_STYLES foregrounds fail WCAG contrast on the resolved "
            f"terminal background ({', '.join(backgrounds)}):\n{joined}"
        )


def format_status(status_name: str) -> str:
    """Format a status name as a Rich-markup string.

    Looks up the status in the default ``STATUS_STYLES`` table
    and returns a string of the form ``[style]icon label[]``
    that Rich can render with the linked style and inline icon.
    Raises ``KeyError`` listing the known statuses when the name
    is unknown.

    Parameters:
        status_name: The status key (e.g. ``"running"``).

    Returns:
        A Rich-markup-formatted string carrying the icon and
        label of the status.
    """
    try:
        style, icon, label = STATUS_STYLES[status_name]
    except KeyError as exc:
        known = ", ".join(sorted(STATUS_STYLES))
        raise KeyError(f"Unknown status {status_name!r}. Known statuses: {known}") from exc
    return f"[{style}]{icon} {label}[]"


def make_console(
    *,
    file: TextIO | None = None,
    no_color: bool | None = None,
    force_terminal: bool | None = None,
    color_system: Literal["auto", "standard", "256", "truecolor", "windows"] | None = None,
    width: int | None = None,
    height: int | None = None,
    terminal_bg_is_light: bool | None = None,
    surface_hex: str | None = None,
) -> Console:
    """Construct a Rich ``Console`` wired with the Ralph theme."""
    resolved_no_color = no_color if no_color is not None else False
    resolved_force_terminal = force_terminal if force_terminal is not None else True
    resolved_color_system: Literal["auto", "standard", "256", "truecolor", "windows"] | None = (
        color_system if color_system is not None else "truecolor"
    )
    if resolved_no_color:
        resolved_color_system = None
    return Console(
        file=file,
        theme=theme_for_background(terminal_bg_is_light, surface_hex=surface_hex),
        no_color=resolved_no_color,
        force_terminal=resolved_force_terminal,
        color_system=resolved_color_system,
        width=width,
        height=height,
        highlight=False,
    )


__all__ = [
    "ASCII_GLYPHS",
    "IDENTITY_PALETTE",
    "IDENTITY_PALETTE_ON_LIGHT_BG",
    "IDENTITY_PALETTE_ON_UNKNOWN_BG",
    "RALPH_THEME",
    "RALPH_THEME_ON_LIGHT_BG",
    "RALPH_THEME_ON_UNKNOWN_BG",
    "STATUS_STYLES",
    "STATUS_STYLES_ON_LIGHT_BG",
    "STATUS_STYLES_ON_UNKNOWN_BG",
    "SYNTAX_BACKGROUND_TRANSPARENT",
    "SYNTAX_THEME_ON_DARK_BG",
    "SYNTAX_THEME_ON_LIGHT_BG",
    "SYNTAX_THEME_ON_UNKNOWN_BG",
    "UNICODE_GLYPHS",
    # Backwards-compatible CVD-matrix aliases (see imports near top).
    "_DEUTERANOPIA_MATRIX",
    "_DIFF_ADDED_FILL_ON_DARK_BG",
    "_DIFF_ADDED_FILL_ON_LIGHT_BG",
    "_DIFF_REMOVED_FILL_ON_DARK_BG",
    "_DIFF_REMOVED_FILL_ON_LIGHT_BG",
    "_PROTANOPIA_MATRIX",
    "_TRITANOPIA_MATRIX",
    "_normalize_identity_name",
    "background_hex_is_light",
    "detect_glyph_capability",
    "detect_terminal_background_hex",
    "detect_terminal_background_is_light",
    "diff_fill_styles",
    "diff_token_foregrounds",
    "display_styles_for_background",
    "format_status",
    "identity_color",
    "make_console",
    "pick_status_styles",
    "preview_background_for_background",
    "preview_foreground_for_background",
    "syntax_theme_for_background",
    "terminal_background_is_light",
    "theme_for_background",
]
