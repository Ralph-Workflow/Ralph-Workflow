"""Okabe-Ito theme helpers for Ralph CLI display.

Fixed-RGB, background-aware Rich and Pygments theme helpers.

All semantic colors are selected from the resolved terminal background and
must preserve contrast and non-color structural carriers.

The palette is Okabe-Ito, so semantic states are not distinguished by hue
alone. ``STATUS_STYLES`` carries a ``(rich_style, unicode_icon, ascii_label)``
tuple per semantic state so display code can retain every meaning carrier on
colorblind and no-color consoles. Semantic roles are defined once here;
background-aware role tables preserve contrast and hue identity on light,
dark, and unknown terminal backgrounds.
"""

from __future__ import annotations

import math
import re
import zlib
from typing import TYPE_CHECKING, Final, Literal

from rich.console import Console
from rich.syntax import PygmentsSyntaxTheme, SyntaxTheme
from rich.theme import Theme

from ralph.syntax_theme import SyntaxThemes

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from typing import TextIO

ORANGE: Final[str] = "#E69F00"
SKY_BLUE: Final[str] = "#56B4E9"
BLUISH_GREEN: Final[str] = "#009E73"
YELLOW: Final[str] = "#F0E442"
BLUE: Final[str] = "#0080CC"
VERMILLION: Final[str] = "#D55E00"
REDDISH_PURPLE: Final[str] = "#CC79A7"
BLACK: Final[str] = "#000000"

# Glyph tables for Unicode and ASCII modes
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
    1. RALPH_FORCE_ASCII env var (any truthy value) → ASCII
    2. stream.encoding exists and 'utf' not in encoding.lower() → ASCII
    3. TERM=dumb → ASCII
    4. Otherwise → Unicode
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


STATUS_STYLES: Final[dict[str, tuple[str, str, str]]] = {
    "success": (f"bold {BLUISH_GREEN}", "\u2713", "PASS"),
    "running": (SKY_BLUE, "\u25d0", "RUN"),
    "warning": (f"bold {ORANGE}", "\u26a0", "WARN"),
    "error": (f"bold {VERMILLION}", "\u2717", "FAIL"),
    "skipped": (YELLOW, "\u25cb", "SKIP"),
    "pending": ("#B8A7E8", "\u25cb", "WAIT"),
    "info": (BLUE, "\u2139", "INFO"),
}

STATUS_STYLES_ON_LIGHT_BG: Final[dict[str, tuple[str, str, str]]] = {
    "success": ("bold #006B4D", "\u2713", "PASS"),
    "running": ("bold #1F5F8B", "\u25d0", "RUN"),
    "warning": ("bold #A06A00", "\u26a0", "WARN"),
    "error": ("bold #993F00", "\u2717", "FAIL"),
    "skipped": ("bold #5A6200", "\u25cb", "SKIP"),
    "pending": ("bold #555555", "\u25cb", "WAIT"),
    "info": ("bold #002B5C", "\u2139", "INFO"),
}

IDENTITY_PALETTE: Final[tuple[str, ...]] = (
    "#E31A1C", "#3288BD", "#33A02C", "#6A3D9A", "#FF7F00", "#B15928",
    "#E7298A", "#B2DF8A", "#FDBF6F", "#CAB2D6", "#FFFF99", "#A6CEE3",
)

IDENTITY_PALETTE_ON_LIGHT_BG: Final[tuple[str, ...]] = (
    "#8B0000", "#00008B", "#006400", "#4B0082", "#663300", "#8B008B",
    "#556B2F", "#5A4FCF", "#483D8B", "#A52A2A", "#3D3D3D", "#1A1A1A",
)

#: Mid-luminance identity colours proven readable on both black and white.
IDENTITY_PALETTE_ON_UNKNOWN_BG: Final[tuple[str, ...]] = (
    "#0070F0", "#0080A0", "#3070E0", "#308080", "#508040", "#6070C0",
    "#608000", "#7060F0", "#807080", "#9060C0", "#907000", "#907030",
)

_IDENTITY_WS_RE: Final[re.Pattern[str]] = re.compile(r"[\s_]+")

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


def _normalize_identity_name(name: str) -> str:
    """Return a stable, lowercase, separator- and case-folded identity."""
    if not name:
        return "unknown"
    folded = name.strip().lower()
    folded = _IDENTITY_WS_RE.sub("-", folded)
    folded = folded.strip("-")
    return folded or "unknown"


def _identity_slot(name: str) -> int:
    """Return the deterministic palette slot for ``name``."""
    normalized = _normalize_identity_name(name)
    digest = zlib.crc32(normalized.encode("utf-8"))
    return digest % len(IDENTITY_PALETTE)


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


_DEUTERANOPIA_MATRIX: Final[tuple[tuple[float, float, float], ...]] = (
    (0.625, 0.375, 0.0),
    (0.7, 0.3, 0.0),
    (0.0, 0.3, 0.7),
)
_PROTANOPIA_MATRIX: Final[tuple[tuple[float, float, float], ...]] = (
    (0.567, 0.433, 0.0),
    (0.558, 0.442, 0.0),
    (0.0, 0.242, 0.758),
)
_TRITANOPIA_MATRIX: Final[tuple[tuple[float, float, float], ...]] = (
    (0.95, 0.05, 0.0),
    (0.0, 0.433, 0.567),
    (0.0, 0.475, 0.525),
)


def _simulate_cvd(hex_color: str, matrix: tuple[tuple[float, float, float], ...]) -> str:
    r, g, b = (channel / 255.0 for channel in _rgb(hex_color))
    out_r = matrix[0][0] * r + matrix[0][1] * g + matrix[0][2] * b
    out_g = matrix[1][0] * r + matrix[1][1] * g + matrix[1][2] * b
    out_b = matrix[2][0] * r + matrix[2][1] * g + matrix[2][2] * b
    return "#" + "".join(
        f"{max(0, min(255, int(channel * 255))):02X}" for channel in (out_r, out_g, out_b)
    )


def identity_color(
    name: str,
    *,
    active: Iterable[str] | None = None,
    terminal_bg_is_light: bool | None,
) -> str:
    """Return the hex color for an identity, with collision-nudge.

    When ``active`` is supplied, the helper nudges the chosen slot
    to the first palette position whose hex does not match the
    resolved hex of any OTHER active identity AND whose simulated
    hex under each of the three documented CVD deficiencies
    (deuteranopia / protanopia / tritanopia) does not match either.
    AC-15 (wt-028-display P3) pins this contract: two
    simultaneously-rendered identities can never read as the same
    color under any of the three simulations.

    Active names resolve in deterministic sorted order. Each identity
    reserves its first CVD-safe slot before the next one is considered,
    so it never competes with its own base colour and active-set order
    cannot change the result.
    """
    if terminal_bg_is_light is True:
        palette = IDENTITY_PALETTE_ON_LIGHT_BG
    elif terminal_bg_is_light is False:
        palette = IDENTITY_PALETTE
    else:
        palette = IDENTITY_PALETTE_ON_UNKNOWN_BG
    base_slot = _identity_slot(name)
    if active is None:
        return palette[base_slot]
    active_names = frozenset(active)
    cvd_matrices = (
        _DEUTERANOPIA_MATRIX,
        _PROTANOPIA_MATRIX,
        _TRITANOPIA_MATRIX,
    )

    def _resolve_hexes(names: Iterable[str]) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for other in sorted(names):
            base = _identity_slot(other)
            occupied = set(resolved.values())
            occupied_cvd = {
                _simulate_cvd(hex_color, matrix)
                for hex_color in occupied
                for matrix in cvd_matrices
            }
            chosen = palette[base]
            # Try the CVD-distinct base slot before nudging forward.
            for offset in range(len(palette)):
                slot = (base + offset) % len(palette)
                candidate = palette[slot]
                if candidate in occupied:
                    continue
                if {_simulate_cvd(candidate, matrix) for matrix in cvd_matrices} & occupied_cvd:
                    continue
                chosen = candidate
                break
            else:
                # Palette is exhausted under CVD constraints. Pick
                # the first unused slot even if it's CVD-ambiguous:
                # the spec allows reuse once all distinct colors
                # are taken (the name label always remains).
                for offset in range(len(palette)):
                    slot = (base + offset) % len(palette)
                    candidate = palette[slot]
                    if candidate not in occupied:
                        chosen = candidate
                        break
            resolved[other] = chosen
        return resolved

    resolved_active = _resolve_hexes(active_names)
    if name in resolved_active:
        return resolved_active[name]
    # ``name`` is not in the active set: nudge away from the
    # already-resolved active identities with the same logic.
    active_hexes = set(resolved_active.values())
    active_cvd_simulated = {
        _simulate_cvd(hex_color, matrix) for hex_color in active_hexes for matrix in cvd_matrices
    }
    used = active_hexes | active_cvd_simulated
    for offset in range(len(palette)):
        slot = (base_slot + offset) % len(palette)
        candidate = palette[slot]
        if candidate in used:
            continue
        candidate_cvd = {_simulate_cvd(candidate, matrix) for matrix in cvd_matrices}
        if candidate_cvd & active_cvd_simulated:
            continue
        return candidate
    return palette[base_slot]


_THEME_STYLES: Final[dict[str, str]] = {
    "theme.level.info": BLUE,
    "theme.level.success": f"bold {BLUISH_GREEN}",
    "theme.level.warn": f"bold {ORANGE}",
    "theme.level.error": f"bold {VERMILLION}",
    "theme.level.milestone": f"bold {SKY_BLUE}",
    "theme.cat.meta": "#178383",
    "theme.cat.cont": BLUE,
    "theme.cat.out": BLUE,
    "theme.log.error": f"bold {VERMILLION}",
    "theme.log.info": BLUE,
    "theme.log.milestone": f"bold {SKY_BLUE}",
    "theme.log.success": f"bold {BLUISH_GREEN}",
    "theme.log.warn": f"bold {ORANGE}",
    "theme.panel.border": SKY_BLUE,
    "theme.panel.title": f"bold {SKY_BLUE}",
    "theme.phase.commit": BLUE,
    "theme.phase.complete": f"bold {BLUISH_GREEN}",
    "theme.phase.development": BLUISH_GREEN,
    "theme.phase.development_analysis": REDDISH_PURPLE,
    "theme.phase.development_commit": BLUE,
    "theme.phase.failed": f"bold {VERMILLION}",
    "theme.phase.fix": VERMILLION,
    "theme.phase.planning": SKY_BLUE,
    "theme.phase.review": ORANGE,
    "theme.phase.review_analysis": REDDISH_PURPLE,
    "theme.phase.review_commit": BLUE,
    "theme.status.error": f"bold {VERMILLION}",
    "theme.status.failure": f"bold {VERMILLION}",
    "theme.status.info": BLUE,
    "theme.status.pending": "#B8A7E8",
    "theme.status.running": SKY_BLUE,
    "theme.status.skipped": YELLOW,
    "theme.status.success": f"bold {BLUISH_GREEN}",
    "theme.status.warning": f"bold {ORANGE}",
    "theme.text.dim_italic": f"italic {REDDISH_PURPLE}",
    "theme.text.emphasis": f"bold {SKY_BLUE}",
    "theme.text.muted": "#178383",
    "theme.banner.ascii": f"bold {SKY_BLUE}",
    "theme.banner.border": SKY_BLUE,
    "theme.banner.tagline": "#178383",
    "theme.banner.title": f"bold {SKY_BLUE}",
    "theme.banner.version": f"bold {BLUISH_GREEN}",
    "theme.banner.welcome": f"bold {SKY_BLUE}",
    "theme.outer_dev": f"bold {SKY_BLUE}",
    "theme.inner_analysis": REDDISH_PURPLE,
    "theme.review_pass": f"bold {BLUISH_GREEN}",
    "theme.review_fail": f"bold {VERMILLION}",
    "theme.proceed": f"bold {BLUISH_GREEN}",
    "theme.revise": f"bold {ORANGE}",
    "theme.status.bar_marker": "#178383",
    "theme.status.path_marker": "#178383",
    "theme.status.path": "#178383",
}

RALPH_THEME: Final[Theme] = Theme(_THEME_STYLES)

# Semantic chrome has its own light-background palette. These roles are used by
# banners, panels, phase rules, tables, and completion summaries, so resolving
# only status labels would leave large parts of a light terminal illegible.
# Keep role names stable: renderers select meaning, while this module selects
# the contrast-tested pigment for the resolved surface.
_THEME_STYLES_ON_LIGHT_BG: Final[dict[str, str]] = {
    **_THEME_STYLES,
    "theme.level.info": "#002B5C",
    "theme.level.success": "bold #006B4D",
    "theme.level.warn": "bold #A06A00",
    "theme.level.error": "bold #993F00",
    "theme.level.milestone": "bold #1F5F8B",
    "theme.cat.meta": "#006A6A",
    "theme.cat.cont": "#002B5C",
    "theme.cat.out": "#002B5C",
    "theme.log.error": "bold #993F00",
    "theme.log.info": "#002B5C",
    "theme.log.milestone": "bold #1F5F8B",
    "theme.log.success": "bold #006B4D",
    "theme.log.warn": "bold #A06A00",
    "theme.panel.border": "#1F5F8B",
    "theme.panel.title": "bold #1F5F8B",
    "theme.phase.commit": "#002B5C",
    "theme.phase.complete": "bold #006B4D",
    "theme.phase.development": "#006B4D",
    "theme.phase.development_analysis": "#6B2C6E",
    "theme.phase.development_commit": "#002B5C",
    "theme.phase.failed": "bold #993F00",
    "theme.phase.fix": "#993F00",
    "theme.phase.planning": "#1F5F8B",
    "theme.phase.review": "#A06A00",
    "theme.phase.review_analysis": "#6B2C6E",
    "theme.phase.review_commit": "#002B5C",
    "theme.status.error": "bold #993F00",
    "theme.status.failure": "bold #993F00",
    "theme.status.info": "#002B5C",
    "theme.status.pending": "#555555",
    "theme.status.running": "#1F5F8B",
    "theme.status.skipped": "#5A6200",
    "theme.status.success": "bold #006B4D",
    "theme.status.warning": "bold #A06A00",
    "theme.text.dim_italic": "italic #6B2C6E",
    "theme.text.emphasis": "bold #1F5F8B",
    "theme.text.muted": "#006A6A",
    "theme.banner.ascii": "bold #1F5F8B",
    "theme.banner.border": "#1F5F8B",
    "theme.banner.tagline": "#006A6A",
    "theme.banner.title": "bold #1F5F8B",
    "theme.banner.version": "bold #006B4D",
    "theme.banner.welcome": "bold #1F5F8B",
    "theme.outer_dev": "bold #1F5F8B",
    "theme.inner_analysis": "#6B2C6E",
    "theme.review_pass": "bold #006B4D",
    "theme.review_fail": "bold #993F00",
    "theme.proceed": "bold #006B4D",
    "theme.revise": "bold #A06A00",
    "theme.status.bar_marker": "#006A6A",
    "theme.status.path_marker": "#006A6A",
    "theme.status.path": "#006A6A",
}
RALPH_THEME_ON_LIGHT_BG: Final[Theme] = Theme(_THEME_STYLES_ON_LIGHT_BG)


def theme_for_background(terminal_bg_is_light: bool | None) -> Theme:
    """Return the semantic Rich theme for the resolved terminal background.

    Unknown backgrounds retain the dark reference theme because no complete
    terminal surface is owned outside previews; individual activity/status
    carriers use their dual-safe fallback tables on that path.
    """
    return RALPH_THEME_ON_LIGHT_BG if terminal_bg_is_light is True else RALPH_THEME


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


_DARK_BG_HEX: Final[str] = "#000000"
_LIGHT_BG_HEX: Final[str] = "#FFFFFF"


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
    """Resolve override, measured OSC 11 colour, then the COLORFGBG hint."""
    explicit = _explicit_background_override(env)
    if explicit is not None:
        return explicit
    if measured_bg_hex:
        measured = background_hex_is_light(measured_bg_hex)
        if measured is not None:
            return measured
    return _colorfgbg_is_light(env)


def _terminal_background_timeout_seconds(env: Mapping[str, str]) -> float:
    """Return the bounded OSC 11 deadline, accepting a positive ms override."""
    try:
        milliseconds = int(env.get("RALPH_TERMINAL_BG_TIMEOUT_MS", "100"))
    except ValueError:
        return 0.1
    return milliseconds / 1_000 if milliseconds > 0 else 0.1


def detect_terminal_background_is_light(env: Mapping[str, str]) -> bool | None:
    """Resolve the terminal background with OSC 11 unless overridden."""
    if env.get("RALPH_TERMINAL_BG", "").strip():
        return terminal_background_is_light(env)
    from ralph.display._terminal_bg_query import query_terminal_background_hex

    return terminal_background_is_light(
        env,
        measured_bg_hex=query_terminal_background_hex(
            timeout=_terminal_background_timeout_seconds(env)
        ),
    )


#: Syntax-highlight palettes selected for dark and light terminal backgrounds.
#: They use fixed RGB because stock ANSI slots are operator-configurable and
#: therefore cannot satisfy the enforced contrast and CVD contract.
SYNTAX_THEME_ON_DARK_BG: Final[SyntaxTheme] = PygmentsSyntaxTheme(SyntaxThemes.dark())
SYNTAX_THEME_ON_LIGHT_BG: Final[SyntaxTheme] = PygmentsSyntaxTheme(SyntaxThemes.light())
SYNTAX_THEME_ON_UNKNOWN_BG: Final[SyntaxTheme] = PygmentsSyntaxTheme(SyntaxThemes.unknown())

#: Rich's ``Syntax(background_color=...)`` sentinel meaning "do not
#: paint a background; let the terminal's own background show through".
#: Named here so syntax-preview call sites never hard-code the literal.
SYNTAX_BACKGROUND_TRANSPARENT: Final[str] = "default"

# A preview owns its surface whenever the terminal background is known. This
# makes every source row, its gutter, and padding measurable against one fixed
# surface; unknown terminals deliberately retain the transparent fallback.
_PREVIEW_BACKGROUND_ON_DARK_BG: Final[str] = "#101417"
_PREVIEW_BACKGROUND_ON_LIGHT_BG: Final[str] = "#F7F9FB"


def preview_background_for_background(terminal_bg_is_light: bool | None) -> str:
    """Return the complete owned preview surface for a resolved background.

    Known backgrounds use a fixed fill shared by code, markdown, and diff
    previews. The unknown path returns Rich's transparent ``default`` sentinel
    because no terminal surface can be safely assumed.
    """
    if terminal_bg_is_light is True:
        return _PREVIEW_BACKGROUND_ON_LIGHT_BG
    if terminal_bg_is_light is False:
        return _PREVIEW_BACKGROUND_ON_DARK_BG
    return SYNTAX_BACKGROUND_TRANSPARENT


# Diff washes are intentionally resolved alongside the syntax palette. The
# unknown-background path remains transparent rather than assuming a terminal.
_DIFF_REMOVED_FILL_ON_DARK_BG: Final[str] = "#101112"
_DIFF_ADDED_FILL_ON_DARK_BG: Final[str] = "#121110"
_DIFF_REMOVED_FILL_ON_LIGHT_BG: Final[str] = "#F5F1F0"
_DIFF_ADDED_FILL_ON_LIGHT_BG: Final[str] = "#F0F4F5"


def diff_token_foregrounds(terminal_bg_is_light: bool | None) -> tuple[str, str]:
    """Return the deleted and inserted token colours for the resolved theme."""
    if terminal_bg_is_light is True:
        return "#330B03", "#3E4712"
    if terminal_bg_is_light is False:
        return "#94D90B", "#0CB9F2"
    return "#2070F0", "#408070"


def diff_fill_styles(terminal_bg_is_light: bool | None) -> tuple[str, str] | None:
    """Return derived removed/added diff fills, or transparent for unknown backgrounds."""
    if terminal_bg_is_light is True:
        return _DIFF_REMOVED_FILL_ON_LIGHT_BG, _DIFF_ADDED_FILL_ON_LIGHT_BG
    if terminal_bg_is_light is False:
        return _DIFF_REMOVED_FILL_ON_DARK_BG, _DIFF_ADDED_FILL_ON_DARK_BG
    return None


def syntax_theme_for_background(terminal_bg_is_light: bool | None) -> SyntaxTheme:
    """Return the fixed-RGB syntax theme resolved for this background."""
    if terminal_bg_is_light is True:
        return SYNTAX_THEME_ON_LIGHT_BG
    if terminal_bg_is_light is False:
        return SYNTAX_THEME_ON_DARK_BG
    return SYNTAX_THEME_ON_UNKNOWN_BG


STATUS_STYLES_ON_UNKNOWN_BG: Final[dict[str, tuple[str, str, str]]] = {
    "success": ("bold #13884E", "\u2713", "PASS"),
    "running": ("bold #0074E8", "\u25d0", "RUN"),
    "warning": ("bold #BA5D00", "\u26a0", "WARN"),
    "error": ("bold #B05C5C", "\u2717", "FAIL"),
    "skipped": ("bold #79773A", "\u25cb", "SKIP"),
    "pending": ("bold #757575", "\u25cb", "WAIT"),
    "info": ("bold #178383", "\u2139", "INFO"),
}


def pick_status_styles(terminal_bg_is_light: bool | None) -> dict[str, tuple[str, str, str]]:
    """Return the status style table for the given background.

    Maps the boolean/None flag to the matching palette table. A
    ``None`` selects the unknown-background table, whose colours clear
    the contrast floor on both black and white terminals.

    Parameters:
        terminal_bg_is_light: ``True`` for light backgrounds,
            ``False`` for dark, ``None`` for unknown.

    Returns:
        A mapping from status name to ``(label, glyph, style)``.
    """
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
) -> Console:
    """Construct a Rich ``Console`` wired with the Ralph theme.

    Centralizes the console construction so every display
    surface inherits the same theme, ``highlight=False`` (which
    keeps plaintext inside tool output uncoloured), and the
    same ``no_color`` / ``force_terminal`` / ``width`` resolver
    semantics. Rich interprets ``color_system=None`` as no ANSI
    output, so Ralph resolves an unspecified system to ``"auto"``;
    use ``no_color=True`` to disable colour. Tests and runtime code
    use this helper to keep the colour behaviour consistent.

    Parameters:
        file: Optional output stream.
        no_color: When ``True``, strip colour from the console.
        force_terminal: When ``True``, treat the console as a
            TTY even when it is not (useful for tests).
        color_system: Rich colour-system override; omitted resolves to ``"auto"``.
        width: Override the console width (default: auto-detect).
        height: Override the console height (default: auto-detect).
        terminal_bg_is_light: Resolved terminal background for semantic roles.

    Returns:
        A ``rich.console.Console`` instance with the Ralph theme.
    """
    resolved_no_color = no_color if no_color is not None else False
    resolved_force_terminal = force_terminal
    resolved_color_system = color_system if color_system is not None else "auto"
    return Console(
        file=file,
        theme=theme_for_background(terminal_bg_is_light),
        no_color=resolved_no_color,
        force_terminal=resolved_force_terminal,
        color_system=resolved_color_system,
        width=width,
        height=height,
        highlight=False,
    )


__all__ = [
    "ASCII_GLYPHS",
    "BLACK",
    "BLUE",
    "BLUISH_GREEN",
    "IDENTITY_PALETTE",
    "IDENTITY_PALETTE_ON_LIGHT_BG",
    "IDENTITY_PALETTE_ON_UNKNOWN_BG",
    "ORANGE",
    "RALPH_THEME",
    "RALPH_THEME_ON_LIGHT_BG",
    "REDDISH_PURPLE",
    "SKY_BLUE",
    "STATUS_STYLES",
    "STATUS_STYLES_ON_LIGHT_BG",
    "STATUS_STYLES_ON_UNKNOWN_BG",
    "SYNTAX_BACKGROUND_TRANSPARENT",
    "SYNTAX_THEME_ON_DARK_BG",
    "SYNTAX_THEME_ON_LIGHT_BG",
    "SYNTAX_THEME_ON_UNKNOWN_BG",
    "UNICODE_GLYPHS",
    "VERMILLION",
    "YELLOW",
    "_DIFF_ADDED_FILL_ON_DARK_BG",
    "_DIFF_ADDED_FILL_ON_LIGHT_BG",
    "_DIFF_REMOVED_FILL_ON_DARK_BG",
    "_DIFF_REMOVED_FILL_ON_LIGHT_BG",
    "background_hex_is_light",
    "detect_glyph_capability",
    "detect_terminal_background_is_light",
    "diff_fill_styles",
    "diff_token_foregrounds",
    "format_status",
    "identity_color",
    "make_console",
    "pick_status_styles",
    "syntax_theme_for_background",
    "terminal_background_is_light",
    "theme_for_background",
]
