"""Okabe-Ito theme helpers for Ralph CLI display.

Accessibility contract
----------------------

The palette is Okabe-Ito (a colorblind-safe palette researched by Okabe
& Ito, 2002 -- "Color Universal Design") so that no semantic state is
distinguishable by hue alone under the common forms of color-vision
deficiency (deuteranopia, protanopia, tritanopia). The pairing rules
below make that explicit so an accessibility decision is made once
here, not per call site:

* ``STATUS_STYLES`` carries a 3-tuple ``(rich_style, unicode_icon,
  ascii_label)`` per semantic state. Display code must always render
  the icon and the ASCII label alongside the color so a colorblind
  operator (or a no-color console) keeps every piece of meaning the
  color was carrying.
* ``success`` (BLUISH_GREEN, okabe-ito #009E73, lightness L*~52)
  vs ``error`` (VERMILLION, okabe-ito #D55E00, lightness L*~54). The
  lightnesses are similar in raw hex but visually distinct after
  Okabe-Ito's perceptual tuning: red/green pairings are the classic
  accessibility trap and the Okabe-Ito pair avoids it by using
  blueish-green and vermillion (orange-red) which differ in lightness
  AND hue. Display code MUST NOT use red/green as the sole
  differentiator for any state pair -- the accessibility test in
  ``tests/display/test_agent_output_accessibility.py`` asserts each
  state also carries a non-color carrier.
* Semantic roles are defined ONCE here: ``success``, ``running``,
  ``warning``, ``error``, ``skipped``, ``pending``, ``info``. New
  states must add a new entry here, never duplicate a hex string in
  a renderer.

Background-aware contrast contract (AC-10)
------------------------------------------

Okabe-Ito's perceptual tuning prioritises *hue distinction* over
*lightness contrast*; on the canonical dark terminal background the
palette clears 4.5:1 WCAG contrast for every state, but on a
*light* background the lighter states (running, warning, skipped,
info) drop below 4.5:1 against white. The accessibility contract
mandates "sufficient contrast on both dark and light terminal
themes" so the single source of truth now exposes two bg-aware
named-role tables:

* :data:`STATUS_STYLES` -- canonical dark-bg reference (the original
  Okabe-Ito hex values, all of which pass 4.5:1 on a black
  background; ``success`` and ``error`` happen to also pass on white
  because their lightness is mid-range).
* :data:`STATUS_STYLES_ON_LIGHT_BG` -- darker Okabe-Ito-derived
  variants picked so each foreground clears 4.5:1 on white. Hue
  identity is preserved: ``success`` stays bluish-green, ``error``
  stays vermillion, etc. so a colourblind operator sees the same
  hue family on either background.

:func:`pick_status_styles` selects the right table at render time
based on the resolved terminal background (light / dark / unknown
auto-detect via :func:`terminal_background_is_light`). Display
callers route through :func:`_state_payload_for_background` /
:func:`status_styles_for_context` so the accessibility decision
lives in this module, not per call site.
"""

from __future__ import annotations

import math
import re
import zlib
from typing import TYPE_CHECKING, ClassVar, Final

from pygments.style import Style as PygmentsStyle
from pygments.token import Comment, Keyword, Name, Number, Operator, String
from rich.console import Console
from rich.syntax import PygmentsSyntaxTheme, SyntaxTheme
from rich.theme import Theme

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

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
    "success": "\u2713",  # ✓
    "error": "\u2717",  # ✗
    "warning": "\u26a0",  # ⚠
    "running": "\u25d0",  # ◐
    "pending": "\u25cb",  # ○
    "info": "i",
    "milestone": "\u25c6",  # ◆
    "arrow": "\u2192",  # →
    "start": "\u25b6",  # ▶
    # New artistic glyphs
    "phase_marker": "\u25a0",  # ■ - phase start marker
    "iteration": "\u21bb",  # ↻ - iteration indicator
    "budget": "\u25b2",  # ▲ - budget indicator
    "review_pass": "\u2714",  # ✔ - review pass
    "review_fail": "\u2718",  # ✘ - review fail
    "outer_dev": "\u25ce",  # ◎ - outer dev indicator (bullseye: clear outer cycle marker)
    "inner_analysis": "\u25b8",  # ▸ - inner analysis indicator (triangle: direction/analysis)
    "proceed": "\u2191",  # ↑ - proceed arrow
    "revise": "\u2193",  # ↓ - revise arrow
    "rule": "\u2500\u2500\u2500",  # ─── - section rule
    # P0 (wt-028-display AC-03): attention-state glyphs. Reuse the
    # existing ``warning`` / ``pending`` / ``iteration`` glyphs where
    # they apply; only the new ``stalled`` and ``terminated`` keys
    # need bespoke entries so the reserved attention slot always has
    # a glyph even when no other glyph applies.
    "stalled": "\u26a0",  # ⚠ - stalled uses the warning glyph
    "terminated": "\u25a0",  # ■ - terminal outcome uses the box marker
    "waiting": "\u25cb",  # ○ - waiting uses the pending glyph
    "retrying": "\u21bb",  # ↻ - retrying uses the iteration glyph
    # wt-028-display S-3: liveness glyph (a spinning-cursor frame)
    # sits between phase and elapsed. The Unicode form is the
    # braille-pattern dot 8 (\u280b) - a low-noise spinner frame
    # that is byte-stable in width across frames (each frame is one
    # cell). The Live tick rewrites the cell per refresh; the
    # byte-stable width is what keeps the neighbours stationary.
    "liveness": "\u280b",  # braille dot 8 (spinner frame 0)
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
    # New ASCII glyphs
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
    # P0 (wt-028-display AC-03): attention-state ASCII glyphs. The
    # ASCII table has to mirror every key in the Unicode table so a
    # ``force_glyphs=False`` context (CI logs, redirect-pipes) still
    # resolves the attention slot to a printable character.
    "stalled": "[!]",
    "terminated": "[OK]",
    "waiting": "[ ]",
    "retrying": "[~]",
    # wt-028-display S-3: ASCII parity for the liveness glyph
    # (mirrors the braille-spinner entry in UNICODE_GLYPHS).
    "liveness": "*",  # ASCII liveness fallback (a single asterisk)
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
    "pending": ("dim", "\u25cb", "WAIT"),
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
    "#E31A1C",
    "#3288BD",
    "#33A02C",
    "#6A3D9A",
    "#FF7F00",
    "#B15928",
    "#E7298A",
    "#B2DF8A",
    "#FDBF6F",
    "#CAB2D6",
    "#FFFF99",
    "#A6CEE3",
)

IDENTITY_PALETTE_ON_LIGHT_BG: Final[tuple[str, ...]] = (
    "#8B0000",
    "#00008B",
    "#006400",
    "#4B0082",
    "#663300",
    "#8B008B",
    "#556B2F",
    "#5A4FCF",
    "#483D8B",
    "#A52A2A",
    "#3D3D3D",
    "#1A1A1A",
)

_IDENTITY_WS_RE: Final[re.Pattern[str]] = re.compile(r"[\s_]+")


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
    for table in (STATUS_STYLES, STATUS_STYLES_ON_LIGHT_BG):
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


def _simulate_cvd(hex_color: str, matrix: Sequence[Sequence[float]]) -> str:
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

    Resolution is two-pass: every name in ``active`` is resolved
    with the deterministic base slot first, then each name is
    nudged so it cannot collide with any other name's RESOLVED
    color. This prevents N identities that share K base slots from
    all landing on the same nudge slot.
    """
    palette = IDENTITY_PALETTE_ON_LIGHT_BG if terminal_bg_is_light else IDENTITY_PALETTE
    base_slot = _identity_slot(name)
    if active is None:
        return palette[base_slot]
    cvd_matrices = (
        _DEUTERANOPIA_MATRIX,
        _PROTANOPIA_MATRIX,
        _TRITANOPIA_MATRIX,
    )

    def _resolve_hexes(names: Iterable[str]) -> dict[str, str]:
        # First pass: deterministic base slot for every name.
        resolved: dict[str, str] = {other: palette[_identity_slot(other)] for other in names}
        # Second pass: nudge each name away from already-resolved
        # peers. We iterate in sorted order so the resolution is
        # stable across runs.
        for other in sorted(resolved):
            base = _identity_slot(other)
            occupied = set(resolved.values())
            occupied_cvd = {
                _simulate_cvd(hex_color, matrix)
                for hex_color in occupied
                for matrix in cvd_matrices
            }
            chosen = palette[base]
            # Try the CVD-distinct slot first.
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

    resolved_active = _resolve_hexes(list(active))
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
    "theme.cat.meta": "dim",
    "theme.cat.cont": BLUE,
    "theme.cat.out": BLUE,
    "theme.log.error": f"bold {VERMILLION}",
    "theme.log.info": BLUE,
    "theme.log.milestone": f"bold {SKY_BLUE}",
    "theme.log.success": f"bold {BLUISH_GREEN}",
    "theme.log.warn": f"bold {ORANGE}",
    "theme.panel.border": BLACK,
    "theme.panel.title": "bold",
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
    "theme.status.pending": "dim",
    "theme.status.running": SKY_BLUE,
    "theme.status.skipped": YELLOW,
    "theme.status.success": f"bold {BLUISH_GREEN}",
    "theme.status.warning": f"bold {ORANGE}",
    "theme.text.dim_italic": "dim italic",
    "theme.text.emphasis": "bold",
    "theme.text.muted": "dim",
    "theme.banner.ascii": f"bold {SKY_BLUE}",
    "theme.banner.border": SKY_BLUE,
    "theme.banner.tagline": "dim",
    "theme.banner.title": f"bold {SKY_BLUE}",
    "theme.banner.version": f"bold {BLUISH_GREEN}",
    "theme.banner.welcome": "bold",
    "theme.outer_dev": f"bold {SKY_BLUE}",
    "theme.inner_analysis": REDDISH_PURPLE,
    "theme.review_pass": f"bold {BLUISH_GREEN}",
    "theme.review_fail": f"bold {VERMILLION}",
    "theme.proceed": f"bold {BLUISH_GREEN}",
    "theme.revise": f"bold {ORANGE}",
    "theme.status.bar_marker": "dim",
    "theme.status.path_marker": "dim",
    "theme.status.path": "dim",
}

RALPH_THEME: Final[Theme] = Theme(_THEME_STYLES)


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
    """Read ``RALPH_TERMINAL_BG`` as a light/dark word or a hex colour."""
    explicit = env.get("RALPH_TERMINAL_BG", "").lower().strip()
    if explicit in {"light", "1", "true", "yes"}:
        return True
    if explicit in {"dark", "0", "false", "no"}:
        return False
    if explicit.startswith("#"):
        return background_hex_is_light(explicit)
    return None


def _colorfgbg_is_light(env: Mapping[str, str]) -> bool | None:
    """Classify the background from the legacy ``COLORFGBG`` hint.

    Only the four palette indices whose lightness is unambiguous are
    honoured; every other value stays undetermined rather than guessed.
    """
    colorfgbg = env.get("COLORFGBG", "").strip()
    if not colorfgbg:
        return None
    parts = colorfgbg.split(";")
    if len(parts) < _COLORFGBG_MIN_PARTS or not parts[1].isdigit():
        return None
    bg_index = int(parts[1])
    if bg_index in (7, 15):
        return True
    if bg_index in (0, 8):
        return False
    return None


def background_hex_is_light(bg_hex: str) -> bool | None:
    """Classify an arbitrary background colour as light or dark.

    Uses the WCAG relative luminance of the colour and the exact
    crossover point at which dark foregrounds start out-contrasting
    light ones, so any background colour -- not just black or white --
    is classified from its measured contrast behaviour.

    Parameters:
        bg_hex: Background colour in ``#rgb`` or ``#rrggbb`` form.

    Returns:
        ``True`` when dark foregrounds read better on this background,
        ``False`` when light foregrounds do, ``None`` when ``bg_hex``
        is not a parseable colour.
    """
    try:
        luminance = relative_luminance(bg_hex)
    except ValueError:
        return None
    return luminance > _LIGHT_BG_LUMINANCE_CROSSOVER


def terminal_background_is_light(
    env: Mapping[str, str],
    *,
    measured_bg_hex: str | None = None,
) -> bool | None:
    """Detect whether the terminal background is light.

    Precedence, strongest signal first:

    1. ``RALPH_TERMINAL_BG`` as an explicit ``light`` / ``dark`` word --
       the operator overriding everything.
    2. ``RALPH_TERMINAL_BG`` as a ``#RRGGBB`` colour -- classified by
       :func:`background_hex_is_light`.
    3. ``measured_bg_hex`` -- the colour the terminal itself reported
       (see :mod:`ralph.display._terminal_bg_query`). This is a
       measurement, so it beats the heuristic below.
    4. ``COLORFGBG`` -- a coarse legacy hint set by only some
       emulators, and usable only for the palette indices whose
       lightness is unambiguous.

    Returns ``None`` when no signal resolves, so the caller can fall
    back to its documented default rather than acting on a guess.

    Parameters:
        env: Mapping of environment variable names to values.
        measured_bg_hex: Background colour measured from the terminal,
            or ``None`` when no measurement is available.

    Returns:
        ``True`` when the background is light, ``False`` when dark,
        ``None`` when undetermined.
    """
    explicit = _explicit_background_override(env)
    if explicit is not None:
        return explicit
    if measured_bg_hex:
        measured = background_hex_is_light(measured_bg_hex)
        if measured is not None:
            return measured
    return _colorfgbg_is_light(env)


def detect_terminal_background_is_light(env: Mapping[str, str]) -> bool | None:
    """Resolve the terminal background, asking the terminal when allowed.

    Wraps :func:`terminal_background_is_light` with the OSC 11 probe so
    the answer comes from the emulator's own reported background colour
    whenever it will tell us. The probe is skipped entirely when an
    explicit ``RALPH_TERMINAL_BG`` override is present (no reason to
    touch the tty when the operator already answered) and degrades to
    ``None`` on every terminal that does not implement the query.

    Parameters:
        env: Mapping of environment variable names to values.

    Returns:
        ``True`` when the background is light, ``False`` when dark,
        ``None`` when undetermined.
    """
    if env.get("RALPH_TERMINAL_BG", "").strip():
        return terminal_background_is_light(env)
    from ralph.display._terminal_bg_query import query_terminal_background_hex

    return terminal_background_is_light(env, measured_bg_hex=query_terminal_background_hex())


class _SyntaxThemeOnDarkBackground(PygmentsStyle):
    """Fixed token palette that clears contrast and CVD checks on dark terminals."""

    default_style: ClassVar[str] = ""
    styles: ClassVar[dict[object, str]] = {
        Comment: "#0CB9F2",
        Keyword: "#B543BF",
        Name.Function: "#6DDCF2",
        String: "#77D9B0",
        Number: "#C9D921",
        Operator: "#94D90B",
    }


class _SyntaxThemeOnLightBackground(PygmentsStyle):
    """Fixed token palette that clears contrast and CVD checks on light terminals."""

    default_style: ClassVar[str] = ""
    styles: ClassVar[dict[object, str]] = {
        Comment: "#854985",
        Keyword: "#251947",
        Name.Function: "#3C7F85",
        String: "#3E4712",
        Number: "#70703E",
        Operator: "#330B03",
    }


class _SyntaxThemeOnUnknownBackground(PygmentsStyle):
    """Mid-luminance tokens readable on both black and white terminals."""

    default_style: ClassVar[str] = ""
    styles: ClassVar[dict[object, str]] = {
        Comment: "#B05C5C",
        Keyword: "#9E684C",
        Name.Function: "#79773A",
        String: "#4E8417",
        Number: "#13884E",
        Operator: "#178383",
    }


#: Syntax-highlight palettes selected for dark and light terminal backgrounds.
#: They use fixed RGB because stock ANSI slots are operator-configurable and
#: therefore cannot satisfy the enforced contrast and CVD contract.
SYNTAX_THEME_ON_DARK_BG: Final[SyntaxTheme] = PygmentsSyntaxTheme(_SyntaxThemeOnDarkBackground)
SYNTAX_THEME_ON_LIGHT_BG: Final[SyntaxTheme] = PygmentsSyntaxTheme(_SyntaxThemeOnLightBackground)
SYNTAX_THEME_ON_UNKNOWN_BG: Final[SyntaxTheme] = PygmentsSyntaxTheme(_SyntaxThemeOnUnknownBackground)

#: Rich's ``Syntax(background_color=...)`` sentinel meaning "do not
#: paint a background; let the terminal's own background show through".
#: Named here so syntax-preview call sites never hard-code the literal.
SYNTAX_BACKGROUND_TRANSPARENT: Final[str] = "default"


def syntax_theme_for_background(terminal_bg_is_light: bool | None) -> SyntaxTheme:
    """Return the code-highlighting theme name for the given background.

    Both returned themes are fixed RGB Pygments styles selected against
    the resolved background. This makes token contrast and CVD separation
    mechanically checkable; stock ANSI slots are operator-configurable and
    cannot provide that guarantee.

    An unknown background (``None``) resolves to a mid-luminance palette
    whose tokens each clear the contrast floor on both black and white.

    Parameters:
        terminal_bg_is_light: ``True`` for light backgrounds,
            ``False`` for dark, ``None`` for unknown.

    Returns:
        The Rich syntax theme to pass to ``rich.syntax.Syntax``.
    """
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
    ``None`` flag selects the dark-background default so callers
    that cannot detect the background still get a contrast-safe
    palette.

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
            ``False`` for dark, ``None`` for unknown (uses dark).
        min_ratio: Minimum acceptable contrast ratio (default
            ``_MIN_CONTRAST_RATIO``).

    Raises:
        RuntimeError: When one or more status styles fail the
            contrast check.
    """
    table = pick_status_styles(terminal_bg_is_light)
    bg_hex = _LIGHT_BG_HEX if terminal_bg_is_light else _DARK_BG_HEX
    failures: list[str] = []
    for state, payload in table.items():
        style = payload[0]
        fg_hex = _extract_hex(style)
        if not fg_hex:
            continue
        ratio = contrast_ratio(fg_hex, bg_hex)
        if ratio < min_ratio:
            failures.append(f"  {state}: {fg_hex} on {bg_hex} = {ratio:.2f}:1 (< {min_ratio})")
    if failures:
        joined = "\n".join(failures)
        raise RuntimeError(
            "STATUS_STYLES foregrounds fail WCAG contrast on the resolved "
            f"terminal background ({bg_hex}):\n{joined}"
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
    no_color: bool | None = None,
    force_terminal: bool | None = None,
    width: int | None = None,
) -> Console:
    """Construct a Rich ``Console`` wired with the Ralph theme.

    Centralizes the console construction so every display
    surface inherits the same theme, ``highlight=False`` (which
    keeps plaintext inside tool output uncoloured), and the
    same ``no_color`` / ``force_terminal`` / ``width`` resolver
    semantics. Tests and runtime code use this helper to keep
    the colour behaviour consistent.

    Parameters:
        no_color: When ``True``, strip colour from the console.
        force_terminal: When ``True``, treat the console as a
            TTY even when it is not (useful for tests).
        width: Override the console width (default: auto-detect).

    Returns:
        A ``rich.console.Console`` instance with the Ralph theme.
    """
    resolved_no_color = no_color if no_color is not None else False
    resolved_force_terminal = force_terminal
    return Console(
        theme=RALPH_THEME,
        no_color=resolved_no_color,
        force_terminal=resolved_force_terminal,
        width=width,
        highlight=False,
    )


__all__ = [
    "ASCII_GLYPHS",
    "BLACK",
    "BLUE",
    "BLUISH_GREEN",
    "IDENTITY_PALETTE",
    "IDENTITY_PALETTE_ON_LIGHT_BG",
    "ORANGE",
    "RALPH_THEME",
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
    "background_hex_is_light",
    "detect_glyph_capability",
    "detect_terminal_background_is_light",
    "format_status",
    "identity_color",
    "make_console",
    "pick_status_styles",
    "syntax_theme_for_background",
    "terminal_background_is_light",
]
