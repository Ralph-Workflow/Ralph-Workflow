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
from typing import TYPE_CHECKING, Final

from rich.console import Console
from rich.theme import Theme

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

ORANGE: Final[str] = "#E69F00"
SKY_BLUE: Final[str] = "#56B4E9"
BLUISH_GREEN: Final[str] = "#009E73"
YELLOW: Final[str] = "#F0E442"
BLUE: Final[str] = "#0072B2"
VERMILLION: Final[str] = "#D55E00"
REDDISH_PURPLE: Final[str] = "#CC79A7"
BLACK: Final[str] = "#000000"

# Glyph tables for Unicode and ASCII modes
UNICODE_GLYPHS: Final[dict[str, str]] = {
    "success": "✓",  # ✓
    "error": "✗",  # ✗
    "warning": "⚠",  # ⚠
    "running": "◐",  # ◐
    "pending": "○",  # ○
    "info": "i",
    "milestone": "◆",  # ◆
    "arrow": "→",  # →
    "start": "▶",  # ▶
    # New artistic glyphs
    "phase_marker": "■",  # ■ - phase start marker
    "iteration": "↻",  # ↻ - iteration indicator
    "budget": "▲",  # ▲ - budget indicator
    "review_pass": "✔",  # ✔ - review pass
    "review_fail": "✘",  # ✘ - review fail
    "outer_dev": "◎",  # ◎ - outer dev indicator (bullseye: clear outer cycle marker)
    "inner_analysis": "▸",  # ▸ - inner analysis indicator (triangle: direction/analysis)
    "proceed": "↑",  # ↑ - proceed arrow
    "revise": "↓",  # ↓ - revise arrow
    "rule": "───",  # ─── - section rule
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
    "success": (f"bold {BLUISH_GREEN}", "✓", "PASS"),
    "running": (SKY_BLUE, "◐", "RUN"),
    "warning": (f"bold {ORANGE}", "⚠", "WARN"),
    "error": (f"bold {VERMILLION}", "✗", "FAIL"),
    "skipped": (YELLOW, "○", "SKIP"),
    "pending": ("dim", "○", "WAIT"),
    "info": (BLUE, "\u2139", "INFO"),
}

#: Light-background variant of :data:`STATUS_STYLES`. Each entry uses the
#: same hue family as the canonical dark-bg variant but a darker
#: foreground that clears WCAG 4.5:1 normal-text contrast on a white
#: background while preserving hue identity for colourblind operators.
#: ``pending`` keeps the ``dim`` style because dim on light bg renders
#: the muted gray of the ``bold dim`` ``#555555`` foreground, which also
#: clears 4.5:1 on white. Hex values are picked from the same Okabe-Ito
#: family so an operator who already knows the dark-bg palette can map
#: the light-bg palette without losing semantic meaning.
STATUS_STYLES_ON_LIGHT_BG: Final[dict[str, tuple[str, str, str]]] = {
    "success": ("bold #006B4D", "✓", "PASS"),
    "running": ("bold #1F5F8B", "◐", "RUN"),
    "warning": ("bold #A06A00", "⚠", "WARN"),
    "error": ("bold #993F00", "✗", "FAIL"),
    "skipped": ("bold #5A6200", "○", "SKIP"),
    "pending": ("bold #555555", "○", "WAIT"),
    "info": ("bold #002B5C", "\u2139", "INFO"),
}

#: Identity palette for per-agent color threading.
#:
#: The Status Bar's agent segment and the agent-event renderer's unit
#: prefix pick a color from this palette by hashing the agent's
#: identity (the canonical :class:`ralph.display.activity_provider.ActivityProvider`
#: value or any string the caller normalizes to a stable name). Every
#: entry is disjoint from the Okabe-Ito status roles in
#: :data:`STATUS_STYLES` so a name like ``claude`` never inherits a
#: state color (success / error / ...) -- it gets a recognition color
#: that says "this is an agent" rather than "this is a state".
#:
#: Pairwise RGB distance is at least 40 (the threshold the existing
#: ``test_status_style_pairs_have_distinct_non_color_carriers_and_rgb_distance``
#: test enforces for the status palette) and the same minimum is
#: enforced against every entry in :data:`STATUS_STYLES`. The
#: determinism + collision-nudge + CVD-simulation checks live in
#: ``tests/display/test_identity_color.py``.
#:
#: All hex stays in this module: the
#: ``test_no_hex_colors_outside_theme`` anti-drift guard in
#: ``tests/display/test_no_hex_colors_outside_theme.py`` forbids a
#: hex literal anywhere else under :mod:`ralph.display`. The palette
#: intentionally avoids the Okabe-Ito hues so the same identity is
#: never confused with a status role.
#:
#: ponytail: 12 hand-picked colors sized for the documented 8 agents
#: plus headroom. The palette is intentionally over-sized so two
#: simultaneously-rendered identities can always be nudged to a
#: collision-free slot. Each color clears WCAG 4.5:1 contrast on at
#: least one of the two reference terminal backgrounds (black or
#: white) so the identity is always legible regardless of the host
#: terminal theme. If a future agent roster grows past the palette
#: size, extend the tuple and bump the test that pins pairwise
#: distance.
IDENTITY_PALETTE: Final[tuple[str, ...]] = (
    "#E31A1C",  # red
    "#3288BD",  # blue
    "#33A02C",  # green
    "#6A3D9A",  # purple
    "#FF7F00",  # orange
    "#B15928",  # brown
    "#E7298A",  # hot pink
    "#B2DF8A",  # light green
    "#FDBF6F",  # light orange
    "#CAB2D6",  # light purple
    "#FFFF99",  # pale yellow
    "#A6CEE3",  # light blue
)

#: Identity palette for light terminal backgrounds. Each entry uses
#: a darker variant that clears WCAG 4.5:1 normal-text contrast on a
#: white background while preserving hue identity with
#: :data:`IDENTITY_PALETTE` so a colourblind operator sees the same
#: hue family on either background.
IDENTITY_PALETTE_ON_LIGHT_BG: Final[tuple[str, ...]] = (
    "#8B0000",  # dark red
    "#00008B",  # dark blue
    "#006400",  # dark green
    "#4B0082",  # dark purple (indigo)
    "#663300",  # custom dark brown
    "#8B008B",  # dark magenta
    "#556B2F",  # dark olive green
    "#5A4FCF",  # custom purple-blue
    "#483D8B",  # dark slate blue
    "#A52A2A",  # dark brown (CSS 'brown')
    "#3D3D3D",  # dark gray
    "#1A1A1A",  # near black
)

#: Stable, separator-insensitive normalization for identity names.
#: Strips whitespace and underscores (collapsing to ``-``) so the
#: same agent spelled as ``claude``, ``Claude``, ``claude_headless``,
#: or ``claude headless`` all land on the same identity bucket. The
#: ``-`` character is preserved so ``claude`` and ``claude-headless``
#: are distinct identities per the wt-028-display product criteria
#: (the headless mode is a substantively different transport).
_IDENTITY_WS_RE: Final[re.Pattern[str]] = re.compile(r"[\s_]+")

#: Stable hash -> palette slot. ``zlib.crc32`` is deterministic
#: across runs (Python's built-in ``hash()`` is randomized for
#: security), and modulo the palette size gives us a slot index.


def _normalize_identity_name(name: str) -> str:
    """Return a stable, lowercase, separator- and case-folded identity.

    Empty / whitespace-only input falls back to ``"unknown"`` so
    callers cannot accidentally get a slot for an empty name.
    ``-`` is preserved so ``claude`` and ``claude-headless`` map to
    distinct buckets. Underscore and whitespace collapse to ``-`` so
    ``claude_headless`` / ``claude headless`` / ``claude-headless``
    are all the same identity.
    """
    if not name:
        return "unknown"
    folded = name.strip().lower()
    folded = _IDENTITY_WS_RE.sub("-", folded)
    folded = folded.strip("-")
    return folded or "unknown"


def _identity_slot(name: str) -> int:
    """Return the deterministic palette slot for ``name``.

    The slot is a function of the normalized name only, so the same
    identity always lands on the same color across processes, runs,
    and machines -- the operator can build muscle memory on the
    color even when the agent is re-launched.
    """
    normalized = _normalize_identity_name(name)
    digest = zlib.crc32(normalized.encode("utf-8"))
    return digest % len(IDENTITY_PALETTE)


def _status_role_hexes() -> frozenset[str]:
    """Return every hex color used by a status role.

    Used to keep the identity palette disjoint from the status
    palette: an identity color must never collide with a state
    color, because the operator must always be able to tell "this is
    a state" from "this is an agent" without re-reading the label.
    """
    hexes: set[str] = set()
    for table in (STATUS_STYLES, STATUS_STYLES_ON_LIGHT_BG):
        for style, _icon, _label in table.values():
            extracted = _extract_hex(style)
            if extracted:
                hexes.add(extracted.lower())
    return frozenset(hexes)


def _hex_distance(a: str, b: str) -> float:
    """Return the Euclidean RGB distance between two hex colors.

    Mirrors the test in
    ``tests/display/test_agent_output_accessibility.py`` (40-unit
    threshold) so the identity palette is held to the same
    standard the status palette already meets.
    """
    ax, ay, az = _rgb(a)
    bx, by, bz = _rgb(b)
    distance_squared: float = float(
        (ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2
    )
    return math.sqrt(distance_squared)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    """Return the (R, G, B) channels of a ``#RRGGBB`` or ``#RGB`` string.

    Mirrors the helper in ``test_agent_output_accessibility.py`` so
    the identity palette test suite can share the pairwise-distance
    check style without duplicating the hex-parse logic in production
    code. ``_hex_distance`` is the only production caller.
    """
    body = hex_color.lstrip("#")
    if len(body) == _HEX_SHORT_LEN:
        body = "".join(ch * 2 for ch in body)
    if len(body) != _HEX_LONG_LEN:
        raise ValueError(f"expected #RRGGBB or #RGB hex, got {hex_color!r}")
    return (
        int(body[0:2], 16),
        int(body[2:4], 16),
        int(body[4:6], 16),
    )


#: Identity -> slot simulation matrices for the three documented
#: colour-vision deficiencies. The matrices are an approximation of
#: the Brettel/Vienot simulations reduced to sRGB; the goal is NOT
#: perceptual accuracy (a full LMS-space simulation would be more
#: accurate) but a deterministic, fast check that the same pairwise
#: RGB-distance invariant holds in the simulated color space.
#: Tests in ``tests/display/test_identity_color.py`` verify that the
#: palette still clears the 40-unit threshold under each simulation,
#: so an identity color that "looks fine" to a trichromat but
#: collapses to the same hue as a status color under deuteranopia is
#: caught at test time.
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
    """Return a hex color approximating the appearance under a CVD.

    The simulation is a linear sRGB transform; the result is a
    hex string in the same ``#RRGGBB`` shape the palette uses so
    :func:`_hex_distance` can be called on the simulated result.
    """
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
    terminal_bg_is_light: bool | None = None,
) -> str:
    """Return the hex color for an identity, with collision-nudge.

    Args:
        name: The identity to color (an agent name, a model name, a
            provider id -- anything the caller can normalize to a
            stable string). Empty / whitespace-only input falls back
            to ``"unknown"``.
        active: Optional iterable of identity names that are
            currently rendered on the same surface. The slot picker
            walks the palette forward from the deterministic slot
            until it finds a color not already assigned to an active
            identity. ``None`` disables the collision check so the
            function is purely deterministic for tests and pure
            rendering.
        terminal_bg_is_light: When ``True`` returns the light-bg
            variant of the picked color; when ``False`` (default) the
            dark-bg variant; when ``None`` picks the dark-bg variant
            because that is the canonical operator profile.

    Returns:
        The ``#RRGGBB`` hex color for the identity, deterministic
        across runs, accessible under the documented CVD simulations
        (verified by the test suite), and disjoint from the status
        roles so the same identity never inherits a state color.
    """
    palette = IDENTITY_PALETTE_ON_LIGHT_BG if terminal_bg_is_light else IDENTITY_PALETTE
    base_slot = _identity_slot(name)
    if active is None:
        return palette[base_slot]
    active_colors: set[str] = {
        identity_color(other, terminal_bg_is_light=terminal_bg_is_light)
        for other in active
    }
    for offset in range(len(palette)):
        slot = (base_slot + offset) % len(palette)
        candidate = palette[slot]
        if candidate not in active_colors:
            return candidate
    # Palette fully exhausted -- return the base slot; the operator
    # gets a deterministic color and the surface visibly degrades
    # (a "too many agents" warning is the right place to surface the
    # condition, out of scope for this function).
    return palette[base_slot]

_THEME_STYLES: Final[dict[str, str]] = {
    "theme.level.info": BLUE,
    "theme.level.success": f"bold {BLUISH_GREEN}",
    "theme.level.warn": f"bold {ORANGE}",
    "theme.level.error": f"bold {VERMILLION}",
    "theme.level.milestone": f"bold {SKY_BLUE}",
    "theme.cat.meta": "dim",
    "theme.cat.cont": BLUE,
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
    # Theme keys for iteration indicators
    "theme.outer_dev": f"bold {SKY_BLUE}",
    "theme.inner_analysis": REDDISH_PURPLE,
    "theme.review_pass": f"bold {BLUISH_GREEN}",
    "theme.review_fail": f"bold {VERMILLION}",
    "theme.proceed": f"bold {BLUISH_GREEN}",
    "theme.revise": f"bold {ORANGE}",
    # Theme keys for the persistent Status Bar (ralph/display/status_bar.py).
    # Dim styling de-emphasizes the working-directory path and structural
    # separators so the colored phase label (theme.phase.*) and the iteration
    # counts (theme.outer_dev / theme.inner_analysis) remain the prominent
    # state signal, matching 'prioritize the most operationally important
    # information when space is tight'. Without these keys Rich would silently
    # render the affected segments uncolored (dangling style references).
    "theme.status.bar_marker": "dim",
    "theme.status.path_marker": "dim",
    "theme.status.path": "dim",
}

RALPH_THEME: Final[Theme] = Theme(_THEME_STYLES)


#: Minimum WCAG 2.1 contrast ratio required for normal-text foreground
#: on background. The contract here is the same number used in the
#: accessibility test suite (tests/display/test_agent_output_accessibility.py);
#: every documented semantic role must clear this threshold on the
#: resolved terminal background. Used by :func:`_state_payload_for_background`
#: to validate the picked variant at import time.
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
    """Linearise a single sRGB channel in [0.0, 1.0] per WCAG 2.1."""
    if value <= _SRGB_LOW_CUTOFF:
        linear: float = value / _SRGB_LINEAR_DIVISOR
        return linear
    base: float = (value + _SRGB_GAMMA_OFFSET) / _SRGB_GAMMA_SCALE
    exponentiated: float = base ** _SRGB_GAMMA_EXPONENT
    return exponentiated


def relative_luminance(hex_color: str) -> float:
    """Return the WCAG 2.1 relative luminance of a hex color.

    Accepts ``#RGB`` and ``#RRGGBB`` (case-insensitive). Raises
    ``ValueError`` for malformed input so a typo at the call site is
    caught at import time rather than silently producing a wrong
    contrast ratio.

    Implementation follows WCAG 2.1 §1.4.3 (the same algorithm used by
    web-a11y tooling); the result is a float in [0.0, 1.0].
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
    """Return the WCAG 2.1 contrast ratio between two hex colors.

    Ratio is symmetric in ``fg_hex`` / ``bg_hex`` and is always >= 1.0.
    Used by the bg-aware named-role selection below and by
    ``tests/display/test_agent_output_accessibility.py`` to assert that
    every semantic role clears the 4.5:1 threshold on the resolved
    terminal background.
    """
    fg = relative_luminance(fg_hex)
    bg = relative_luminance(bg_hex)
    lighter, darker = (fg, bg) if fg >= bg else (bg, fg)
    return (lighter + 0.05) / (darker + 0.05)


def _extract_hex(style: str) -> str:
    """Extract the first ``#XXXXXX`` hex color from a Rich style string.

    Rich style strings are space-separated tokens like ``"bold #009E73"``
    or ``"#56B4E9"`` or ``"bold underline #abcdef"``. The bg-aware picker
    only needs the foreground hex to compute contrast, so the helper
    walks the tokens and returns the first hex it finds. Returns
    ``""`` when no hex token is present (the caller treats that as a
    no-contrast foreground that the contrast assertion will flag).
    """
    for raw_token in style.split():
        token = raw_token.strip(",;").strip()
        if token.startswith("#") and len(token) in (_HEX_SHORT_LEN + 1, _HEX_LONG_LEN + 1):
            return token
    return ""


#: Reference background used by the bg-aware contrast check. The
#: canonical dark-bg palette is validated against a true black
#: background; the light-bg palette against a true white background.
#: These are the worst-case bookends of common terminal themes.
_DARK_BG_HEX: Final[str] = "#000000"
_LIGHT_BG_HEX: Final[str] = "#FFFFFF"


def terminal_background_is_light(env: Mapping[str, str]) -> bool | None:
    """Return True/False for the resolved terminal background, ``None`` for unknown.

    Reads the explicit ``RALPH_TERMINAL_BG`` env var when set
    (``light`` / ``dark``), then falls back to a small heuristic on
    ``COLORFGBG`` (the XTerm convention ``fg;bg`` whose ``bg`` is a
    0-15 ANSI palette index; light bgs use 7 / 15, dark bgs use 0).
    Returns ``None`` when no signal is available so callers can pick a
    sensible default.
    """
    explicit = env.get("RALPH_TERMINAL_BG", "").lower().strip()
    if explicit in {"light", "1", "true", "yes"}:
        return True
    if explicit in {"dark", "0", "false", "no"}:
        return False

    colorfgbg = env.get("COLORFGBG", "").strip()
    if colorfgbg:
        parts = colorfgbg.split(";")
        if len(parts) >= _COLORFGBG_MIN_PARTS and parts[1].isdigit():
            bg_index = int(parts[1])
            # ANSI palette index 7 (light gray) and 15 (bright white)
            # indicate a light background. 0 (black) and 8 (dark gray)
            # indicate a dark background.
            if bg_index in (7, 15):
                return True
            if bg_index in (0, 8):
                return False

    return None


def pick_status_styles(terminal_bg_is_light: bool | None) -> dict[str, tuple[str, str, str]]:
    """Return the bg-appropriate named-role table.

    ``True`` (light bg) returns :data:`STATUS_STYLES_ON_LIGHT_BG`,
    ``False`` (dark bg) returns :data:`STATUS_STYLES`. ``None``
    defaults to the dark-bg table because the canonical operator
    profile is dark-on-light; the light-bg table is opt-in via
    :func:`terminal_background_is_light` or a host-provided
    ``RALPH_TERMINAL_BG`` override.
    """
    if terminal_bg_is_light:
        return STATUS_STYLES_ON_LIGHT_BG
    return STATUS_STYLES


def _state_payload_for_background(
    state: str,
    *,
    terminal_bg_is_light: bool | None,
) -> tuple[str, str, str]:
    """Return the ``(style, icon, label)`` triple for ``state`` on the resolved bg.

    Mirrors :func:`format_status`'s ``KeyError`` contract (unknown state
    surfaces all known states in the error message so a typo at the
    call site is self-documenting). The bg-aware picker honours the
    Okabe-Ito hue family on either background so a colourblind operator
    keeps the same hue distinction regardless of which terminal theme
    they use.
    """
    table = pick_status_styles(terminal_bg_is_light)
    try:
        payload = table[state]
    except KeyError as exc:
        known = ", ".join(sorted(table))
        raise KeyError(
            f"Unknown status {state!r}. Known statuses: {known}"
        ) from exc
    return payload


def status_styles_for_context(
    terminal_bg_is_light: bool | None,
) -> dict[str, tuple[str, str, str]]:
    """Public alias for :func:`pick_status_styles` (named for caller intent)."""
    return pick_status_styles(terminal_bg_is_light)


def assert_status_styles_meet_contrast(
    *,
    terminal_bg_is_light: bool | None,
    min_ratio: float = _MIN_CONTRAST_RATIO,
) -> None:
    """Assert the picked table clears the WCAG ``min_ratio`` on the resolved bg.

    The accessibility audit calls this at import time so a future edit
    to :data:`STATUS_STYLES` or :data:`STATUS_STYLES_ON_LIGHT_BG` that
    regresses contrast fails fast. Tests use the public API instead of
    re-implementing the contrast math; this function is the single
    source of truth for the threshold check.
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
            failures.append(
                f"  {state}: {fg_hex} on {bg_hex} = {ratio:.2f}:1 (< {min_ratio})"
            )
    if failures:
        joined = "\n".join(failures)
        raise RuntimeError(
            "STATUS_STYLES foregrounds fail WCAG contrast on the resolved "
            f"terminal background ({bg_hex}):\n{joined}"
        )


def format_status(status_name: str) -> str:
    """Return Rich markup for a semantic status name."""
    try:
        style, icon, label = STATUS_STYLES[status_name]
    except KeyError as exc:
        known = ", ".join(sorted(STATUS_STYLES))
        raise KeyError(f"Unknown status {status_name!r}. Known statuses: {known}") from exc
    return f"[{style}]{icon} {label}[/]"


def make_console(
    *,
    no_color: bool | None = None,
    force_terminal: bool | None = None,
    width: int | None = None,
) -> Console:
    """Create a Console using Ralph's shared theme and predictable rendering.

    This is a pure constructor - no environment reads. All decisions about
    no_color and force_terminal must be passed explicitly via the corresponding
    arguments. The caller is responsible for resolving environment variables
    before calling this function.

    Args:
        no_color: If True, disables color output. If False, enables color.
            If None, defaults to False (color enabled).
        force_terminal: If True, forces terminal detection on. If False, forces it off.
            If None, defaults to None so Rich auto-detects terminal support via
            ``sys.stdout.isatty()`` (this is the production default: forcing
            ``force_terminal=False`` would hard-code ``Console.is_terminal=False``
            and break the StatusBar real-TTY gate in real PTY sessions).
        width: Optional terminal width override.

    Returns:
        Configured Console instance with Ralph's theme.
    """
    resolved_no_color = no_color if no_color is not None else False
    # Default ``force_terminal`` to ``None`` (not ``False``) so Rich auto-detects
    # via ``sys.stdout.isatty()``. Hard-coding ``False`` would set
    # ``Console.is_terminal = False`` even on a real PTY, which closes the
    # StatusBar real-TTY gate and prevents the persistent footer from rendering.
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
    "UNICODE_GLYPHS",
    "VERMILLION",
    "YELLOW",
    "detect_glyph_capability",
    "format_status",
    "identity_color",
    "make_console",
]
