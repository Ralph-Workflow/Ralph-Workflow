"""Deterministic Monokai Pro-derived palette generator with WCAG 4.5:1 contrast floor."""

from __future__ import annotations

import bisect
import functools
import math
from typing import Final, NamedTuple


class RoleAnchor(NamedTuple):
    """Semantic role anchor holding OKLCh hue, target chroma, and Monokai
    Pro's own OKLab lightness for the colour this role stands for.

    ``l_ref`` is measured on the same reference background
    (``_CANONICAL_DARK_SURFACE_HEX`` / Monokai Pro's own ``#2D2A2E``) that
    ``_REFERENCE_BACKGROUND_L`` records, so ``l_ref - _REFERENCE_BACKGROUND_L``
    is a *portable* offset: the distance (in OKLab L) this role sits from a
    dark reference background in Monokai Pro's own palette. ``solve_for_surface``
    re-applies that same offset to whatever surface it is actually asked to
    solve against, which is what reproduces Monokai Pro's own lightness
    structure instead of collapsing every role onto the single lightness that
    just barely clears the WCAG floor.
    """

    hue: float
    chroma: float
    l_ref: float


# --- Color Space Conversions ---
# (Defined ahead of ROLE_ANCHORS so the anchors' l_ref fields can be measured
# directly from the Monokai Pro reference hexes below, rather than hand-copied
# as rounded literals that could silently drift from the hexes they claim to
# measure.)

_SRGB_THRESHOLD: Final[float] = 0.04045
_LINEAR_THRESHOLD: Final[float] = 0.0031308
_SHORT_HEX_LEN: Final[int] = 3


def srgb_to_linear(c: float) -> float:
    """Convert standard sRGB channel [0, 1] to linear sRGB."""
    if c <= _SRGB_THRESHOLD:
        return c / 12.92
    return math.pow((c + 0.055) / 1.055, 2.4)


def linear_to_srgb(c: float) -> float:
    """Convert linear sRGB channel [0, 1] to standard sRGB."""
    if c <= _LINEAR_THRESHOLD:
        return 12.92 * c
    return 1.055 * math.pow(c, 1.0 / 2.4) - 0.055


def hex_to_rgb(hex_str: str) -> tuple[float, float, float]:
    """Parse #RRGGBB or #RGB into float sRGB tuple in [0, 1]."""
    cleaned = hex_str.strip().lstrip("#")
    if len(cleaned) == _SHORT_HEX_LEN:
        cleaned = "".join(ch * 2 for ch in cleaned)
    r = int(cleaned[0:2], 16) / 255.0
    g = int(cleaned[2:4], 16) / 255.0
    b = int(cleaned[4:6], 16) / 255.0
    return r, g, b


def rgb_to_hex(r: float, g: float, b: float) -> str:
    """Convert float sRGB tuple in [0, 1] to uppercase #RRGGBB."""
    r_int = max(0, min(255, round(r * 255.0)))
    g_int = max(0, min(255, round(g * 255.0)))
    b_int = max(0, min(255, round(b * 255.0)))
    return f"#{r_int:02X}{g_int:02X}{b_int:02X}"


def rgb_to_oklab(r: float, g: float, b: float) -> tuple[float, float, float]:
    """Convert sRGB float tuple to OKLab (L, a, b)."""
    lr = srgb_to_linear(r)
    lg = srgb_to_linear(g)
    lb = srgb_to_linear(b)

    l_lms = 0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb
    m_lms = 0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb
    s_lms = 0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb

    l_ = math.pow(l_lms, 1.0 / 3.0) if l_lms >= 0 else -math.pow(-l_lms, 1.0 / 3.0)
    m_ = math.pow(m_lms, 1.0 / 3.0) if m_lms >= 0 else -math.pow(-m_lms, 1.0 / 3.0)
    s_ = math.pow(s_lms, 1.0 / 3.0) if s_lms >= 0 else -math.pow(-s_lms, 1.0 / 3.0)

    lab_l = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720403 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    b_lab = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757986 * s_
    return lab_l, a, b_lab


def oklab_to_rgb(l_val: float, a: float, b_lab: float) -> tuple[float, float, float]:
    """Convert OKLab (L, a, b) to sRGB float tuple."""
    l_ = l_val + 0.3963377774 * a + 0.2158037573 * b_lab
    m_ = l_val - 0.1055613458 * a - 0.0638541728 * b_lab
    s_ = l_val - 0.0894841775 * a - 1.2914855480 * b_lab

    l_lms = math.pow(l_, 3.0)
    m_lms = math.pow(m_, 3.0)
    s_lms = math.pow(s_, 3.0)

    lr = +4.0767416621 * l_lms - 3.3077115913 * m_lms + 0.2309699292 * s_lms
    lg = -1.2684380046 * l_lms + 2.6097574011 * m_lms - 0.3413193965 * s_lms
    lb = -0.0041960863 * l_lms - 0.7034186147 * m_lms + 1.7076147010 * s_lms

    r = linear_to_srgb(lr)
    g = linear_to_srgb(lg)
    b = linear_to_srgb(lb)
    return r, g, b


def oklab_to_oklch(l_val: float, a: float, b_lab: float) -> tuple[float, float, float]:
    """Convert OKLab (L, a, b) to OKLCh (L, C, h)."""
    chroma = math.hypot(a, b_lab)
    h = math.degrees(math.atan2(b_lab, a)) % 360.0
    return l_val, chroma, h


def oklch_to_oklab(l_val: float, c_val: float, h: float) -> tuple[float, float, float]:
    """Convert OKLCh (L, C, h) to OKLab (L, a, b)."""
    rad = math.radians(h)
    a = c_val * math.cos(rad)
    b_lab = c_val * math.sin(rad)
    return l_val, a, b_lab


def is_in_gamut(r: float, g: float, b: float, tol: float = 1e-4) -> bool:
    """Return True if sRGB float tuple is within [0, 1]."""
    return (-tol <= r <= 1.0 + tol) and (-tol <= g <= 1.0 + tol) and (-tol <= b <= 1.0 + tol)


def relative_luminance(r: float, g: float, b: float) -> float:
    """Calculate WCAG relative luminance Y for sRGB tuple."""
    lr = srgb_to_linear(r)
    lg = srgb_to_linear(g)
    lb = srgb_to_linear(b)
    return 0.2126 * lr + 0.7152 * lg + 0.0722 * lb


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    """Calculate WCAG contrast ratio between two hex strings."""
    y_a = relative_luminance(*hex_to_rgb(hex_a))
    y_b = relative_luminance(*hex_to_rgb(hex_b))
    lighter = max(y_a, y_b)
    darker = min(y_a, y_b)
    return (lighter + 0.05) / (darker + 0.05)


def _oklab_l_of_hex(hex_str: str) -> float:
    """Measure the OKLab L of a fixed reference hex, for seeding a RoleAnchor."""
    lab_l, _a, _b = rgb_to_oklab(*hex_to_rgb(hex_str))
    return lab_l


#: Monokai Pro's own background (Octagon filter). The reference every
#: RoleAnchor.l_ref is measured against, and the surface identity/preview
#: fills are derived from. Kept here (rather than duplicated in
#: ``ralph.display.theme``) so both this module and ``theme``/``syntax_theme``/
#: ``_markdown_theme`` share the single literal.
_CANONICAL_DARK_SURFACE_HEX: Final[str] = "#2D2A2E"
#: The corresponding hardest common light surface, just under pure white --
#: see ``ralph.display.theme``'s module docstring for the DA-001 rationale
#: (both constants now live here so ``theme``/``syntax_theme``/
#: ``_markdown_theme`` can single-source them without a circular import).
_CANONICAL_LIGHT_SURFACE_HEX: Final[str] = "#FAF8F5"
#: WCAG crossover luminance, matching ``ralph.display.theme``'s own
#: crossover so a preview fill derived here and one derived through
#: ``theme.py`` never disagree on which side of "light" a surface falls.
_LIGHT_BG_LUMINANCE_CROSSOVER: Final[float] = 0.1791

#: The reference background's own OKLab lightness. Every RoleAnchor.l_ref -
#: _REFERENCE_BACKGROUND_L is the *offset* solve_for_surface re-applies to an
#: arbitrary surface (see RoleAnchor's docstring).
_REFERENCE_BACKGROUND_L: Final[float] = _oklab_l_of_hex(_CANONICAL_DARK_SURFACE_HEX)


ROLE_ANCHORS: Final[dict[str, RoleAnchor]] = {
    "success": RoleAnchor(hue=130.7, chroma=0.142, l_ref=_oklab_l_of_hex("#A9DC76")),
    "error": RoleAnchor(hue=8.5, chroma=0.194, l_ref=_oklab_l_of_hex("#FF6188")),
    "warning": RoleAnchor(hue=46.2, chroma=0.136, l_ref=_oklab_l_of_hex("#FC9867")),
    "skipped": RoleAnchor(hue=90.5, chroma=0.139, l_ref=_oklab_l_of_hex("#FFD866")),
    "info": RoleAnchor(hue=205.7, chroma=0.095, l_ref=_oklab_l_of_hex("#78DCE8")),
    "running": RoleAnchor(hue=205.7, chroma=0.095, l_ref=_oklab_l_of_hex("#78DCE8")),
    "pending": RoleAnchor(hue=290.7, chroma=0.121, l_ref=_oklab_l_of_hex("#AB9DF2")),
    "analysis": RoleAnchor(hue=290.7, chroma=0.121, l_ref=_oklab_l_of_hex("#AB9DF2")),
    # `agent_text` is body text, not a state carrier -- it takes the new
    # `foreground` anchor's own hue/chroma/l_ref rather than duplicating
    # `info`'s, which is what makes it distinguishable from `chrome`
    # (S-5 repoints ralph.display.theme's role -> anchor mapping to match).
    "agent_text": RoleAnchor(hue=106.45, chroma=0.0026, l_ref=_oklab_l_of_hex("#FCFCFA")),
    "chrome": RoleAnchor(hue=205.7, chroma=0.095, l_ref=_oklab_l_of_hex("#78DCE8")),
    "elision": RoleAnchor(hue=290.7, chroma=0.121, l_ref=_oklab_l_of_hex("#AB9DF2")),
    # `muted` has no Monokai Pro (hue, chroma) twin -- it is the deliberately
    # recessive role. Its l_ref is *derived*, not measured: the midpoint
    # between the reference background's own lightness and `comment`'s, so it
    # reads as receding below `comment` without landing on `info`/`chrome`'s
    # plane (no anchor shares its hue at a different l_ref) or vanishing into
    # the background itself.
    "muted": RoleAnchor(
        hue=205.7,
        chroma=0.060,
        l_ref=(_REFERENCE_BACKGROUND_L + _oklab_l_of_hex("#727072")) / 2.0,
    ),
    "diff_added": RoleAnchor(hue=130.7, chroma=0.142, l_ref=_oklab_l_of_hex("#A9DC76")),
    "diff_removed": RoleAnchor(hue=8.5, chroma=0.194, l_ref=_oklab_l_of_hex("#FF6188")),
    # Plain text, identifiers, Markdown body -- Monokai Pro's own near-white
    # foreground, not a hue accent.
    "foreground": RoleAnchor(hue=106.45, chroma=0.0026, l_ref=_oklab_l_of_hex("#FCFCFA")),
    # Deliberate deviation from Monokai Pro: `#727072` measures only 2.88:1 on
    # `#2D2A2E`, below the 4.5:1 WCAG floor this module enforces, so
    # `solve_for_surface` lifts `comment` to the floor rather than
    # reproducing Monokai Pro's own dimmed grey bit-for-bit. `l_ref` still
    # records the true Monokai Pro measurement -- it is the floor clamp in
    # `solve_for_surface`, not this anchor, that performs the lift.
    "comment": RoleAnchor(hue=325.63, chroma=0.0039, l_ref=_oklab_l_of_hex("#727072")),
}

_CONTRAST_FLOOR: Final[float] = 4.5


# --- Solvers ---


def _oklch_to_rgb_clamped(l_val: float, c_val: float, h: float) -> tuple[float, float, float]:
    """Convert OKLCh to in-gamut sRGB tuple by clamping chroma down if needed."""
    r, g, b = oklab_to_rgb(*oklch_to_oklab(l_val, c_val, h))
    if is_in_gamut(r, g, b):
        return r, g, b

    low_c, high_c = 0.0, c_val
    best_r, best_g, best_b = oklab_to_rgb(*oklch_to_oklab(l_val, 0.0, h))

    for _ in range(16):
        mid_c = (low_c + high_c) / 2.0
        mr, mg, mb = oklab_to_rgb(*oklch_to_oklab(l_val, mid_c, h))
        if is_in_gamut(mr, mg, mb):
            best_r, best_g, best_b = mr, mg, mb
            low_c = mid_c
        else:
            high_c = mid_c

    return (
        max(0.0, min(1.0, best_r)),
        max(0.0, min(1.0, best_g)),
        max(0.0, min(1.0, best_b)),
    )


def _floor_lightness(h: float, target_chroma: float, y_surf: float, min_ratio: float, *, lighter: bool) -> float:
    """Binary-search the minimal (lighter) or maximal (darker) OKLab L that
    clears ``min_ratio`` contrast against a surface of luminance ``y_surf``.

    This is the WCAG floor as a floor: the smallest lightness move away from
    the surface that still clears the contrast minimum, independent of any
    Monokai Pro reference lightness. ``solve_for_surface`` takes the
    less-extreme of this and the reference-offset target, so the floor is
    never *undershot*, and the Monokai Pro structure is never needlessly
    *overridden* when it already clears the floor on its own.
    """
    if lighter:
        target_y = min(1.0, max(0.0, min_ratio * (y_surf + 0.05) - 0.05))
        low_l, high_l = 0.0, 1.0
        best_l = 1.0
        for _ in range(24):
            mid_l = (low_l + high_l) / 2.0
            r, g, b = _oklch_to_rgb_clamped(mid_l, target_chroma, h)
            y = relative_luminance(r, g, b)
            if y >= target_y:
                best_l = mid_l
                high_l = mid_l
            else:
                low_l = mid_l
        return best_l
    target_y = max(0.0, min(1.0, (y_surf + 0.05) / min_ratio - 0.05))
    low_l, high_l = 0.0, 1.0
    best_l = 0.0
    for _ in range(24):
        mid_l = (low_l + high_l) / 2.0
        r, g, b = _oklch_to_rgb_clamped(mid_l, target_chroma, h)
        y = relative_luminance(r, g, b)
        if y <= target_y:
            best_l = mid_l
            low_l = mid_l
        else:
            high_l = mid_l
    return best_l


#: Every role's reference offset (``l_ref - _REFERENCE_BACKGROUND_L``),
#: sorted ascending. ``_offset_rank`` uses this fixed, surface-independent
#: ordering -- not raw offset magnitude -- to place out-of-band roles,
#: because two roles can share an almost identical offset (Monokai Pro's own
#: ``success``/``info`` sit within 0.002 OKLab L of each other) and a
#: magnitude-proportional placement would still land them on nearly
#: identical compressed lightnesses.
_ROLE_OFFSETS_SORTED: Final[tuple[float, ...]] = tuple(
    sorted(a.l_ref - _REFERENCE_BACKGROUND_L for a in ROLE_ANCHORS.values())
)

#: How far past the floor, in absolute OKLab L, an out-of-band role's rank
#: is allowed to push it -- capped, never a fraction of whatever headroom
#: happens to remain (measured empirically: at OKLab L within ~0.12 of a
#: floor around L=0.2, hue is still faintly legible after gamut clamping;
#: much further and it is not). The floor is where each role's own target
#: chroma survives *best* (moving further from it only tightens the gamut
#: further -- see ``solve_for_surface``'s docstring), so letting ranks span
#: the *entire* remaining band would push the highest-ranked out-of-band
#: roles into a gamut region so tight that hue stops mattering (every hue
#: rounds to the same near-black/near-white 8-bit pixel there). An absolute
#: cap -- rather than a fraction of headroom -- matters because headroom
#: itself can be tiny (a surface whose luminance sits right at the edge of
#: where a polarity is even viable can leave under 0.02 of OKLab L to work
#: with); on those surfaces the cap does not bind and the rank spreads
#: across all the headroom there is, which is exactly what is needed there.
_COMPRESSION_REACH_ABSOLUTE: Final[float] = 0.12


def _offset_rank(offset: float) -> float:
    """Return ``offset``'s rank among every role's reference offset,
    normalised to [0, 1]. Ties (roles that deliberately share an anchor,
    e.g. ``chrome``/``info``) average to the same rank, by design -- they
    are meant to resolve identically, not to be pried apart."""
    lo_idx = bisect.bisect_left(_ROLE_OFFSETS_SORTED, offset)
    hi_idx = bisect.bisect_right(_ROLE_OFFSETS_SORTED, offset) - 1
    rank = (lo_idx + hi_idx) / 2.0
    denom = len(_ROLE_OFFSETS_SORTED) - 1
    return rank / denom if denom > 0 else 0.0


def _nudge_to_floor(l_val: float, h: float, target_chroma: float, surface_hex: str, min_ratio: float, *, lighter: bool) -> str:
    """Step ``l_val`` further from the surface until contrast clears
    ``min_ratio``, as a last-resort safety net after gamut clamping."""
    r, g, b = _oklch_to_rgb_clamped(l_val, target_chroma, h)
    res_hex = rgb_to_hex(r, g, b)
    step = 0.005
    while contrast_ratio(res_hex, surface_hex) < min_ratio and 0.0 < l_val < 1.0:
        l_val = min(1.0, l_val + step) if lighter else max(0.0, l_val - step)
        r, g, b = _oklch_to_rgb_clamped(l_val, target_chroma, h)
        res_hex = rgb_to_hex(r, g, b)
        step *= 1.2
    return res_hex


def solve_for_surface(
    anchor: RoleAnchor, surface_hex: str, min_ratio: float = _CONTRAST_FLOOR
) -> str:
    """Solve a role's colour against ``surface_hex``.

    Aims at Monokai Pro's own lightness structure and only moves off it when
    accessibility demands: the target lightness is the surface's own OKLab L
    shifted by this role's *reference offset* (``anchor.l_ref -
    _REFERENCE_BACKGROUND_L``), which reproduces Monokai Pro's palette
    exactly on the reference surface and preserves its relative spacing
    elsewhere. The WCAG floor is enforced as a floor, not a target: when the
    reference-offset target already clears ``min_ratio`` (lands inside the
    feasible band between the floor and the gamut boundary), it is used
    as-is (this is what restores the lightness spread Characterize point 1
    describes as collapsed onto one plane).

    When it does not -- the offset target falls outside the feasible band,
    which happens on narrow-headroom surfaces (mid-greys) where the floor
    itself sits close to the gamut boundary, or where the raw offset target
    overshoots past pure black/white -- every role that falls outside is
    *not* clipped to the same boundary value. Clipping was the original
    defect: on a surface where the floor is, say, L=0.97, every role whose
    offset target landed below that collapsed onto the identical L=0.97, and
    at lightness that extreme the sRGB gamut only tolerates near-zero chroma
    regardless of hue, so distinct hues collapsed onto the same pixel.
    Stretching every out-of-band role uniformly across the *entire* [0,
    floor] / [floor, 1] band is not the fix either: most of that band is far
    from the floor, where the gamut is just as tight as it is at 0/1 (the
    floor is, by construction, the *least* extreme point that still clears
    contrast -- it is where each role's own target chroma survives best;
    moving further away only tightens the gamut further). Nor is
    magnitude-proportional placement enough on its own -- see
    ``_ROLE_OFFSETS_SORTED``'s docstring for why. Instead, each out-of-band
    role is placed by its fixed, surface-independent offset **rank**
    (``_offset_rank``) among every role, spread only across the bounded
    region just past the floor that ``_COMPRESSION_REACH_ABSOLUTE`` still
    keeps hue-legible, rather than the full remaining band down to 0/up to
    1.
    """
    surf_r, surf_g, surf_b = hex_to_rgb(surface_hex)
    y_surf = relative_luminance(surf_r, surf_g, surf_b)

    max_upper_ratio = 1.05 / (y_surf + 0.05)
    max_lower_ratio = (y_surf + 0.05) / 0.05

    lighter = max_upper_ratio >= min_ratio and (
        max_upper_ratio >= max_lower_ratio or max_lower_ratio < min_ratio
    )

    h = anchor.hue
    target_chroma = anchor.chroma

    surface_l, _surface_a, _surface_b = rgb_to_oklab(surf_r, surf_g, surf_b)
    offset = anchor.l_ref - _REFERENCE_BACKGROUND_L
    offset_l_raw = surface_l + offset if lighter else surface_l - offset

    floor_l = _floor_lightness(h, target_chroma, y_surf, min_ratio, lighter=lighter)

    # Feasible band is [floor_l, 1.0] when lighter, [0.0, floor_l] when
    # darker. Use the offset target as-is when it already lands inside it;
    # otherwise place this role by rank, reaching at most
    # _COMPRESSION_REACH_ABSOLUTE past the floor (or all remaining headroom,
    # whichever is smaller) -- toward 1.0 when lighter, toward 0.0 when
    # darker.
    band_lo, band_hi = (floor_l, 1.0) if lighter else (0.0, floor_l)
    if band_lo <= offset_l_raw <= band_hi:
        best_l = offset_l_raw
    elif lighter:
        rank = _offset_rank(offset)
        best_l = floor_l + rank * min(_COMPRESSION_REACH_ABSOLUTE, 1.0 - floor_l)
    else:
        rank = _offset_rank(offset)
        best_l = floor_l - rank * min(_COMPRESSION_REACH_ABSOLUTE, floor_l)

    return _nudge_to_floor(best_l, h, target_chroma, surface_hex, min_ratio, lighter=lighter)


def solve_dual_safe(anchor: RoleAnchor) -> str:
    """Target the [0.175, 0.1833] dual-safe luminance band for undetermined
    surfaces, ordered by each role's reference offset so roles do not
    collapse onto one plane even though the band is too narrow to carry
    Monokai Pro's full lightness spread (both endpoints of the band already
    clear 4.5:1 against #000000 and #FFFFFF by construction -- see the
    derivation of ``_DUAL_SAFE_*`` below)."""
    h = anchor.hue
    target_chroma = anchor.chroma

    offset = anchor.l_ref - _REFERENCE_BACKGROUND_L
    normalized = (offset - _DUAL_SAFE_OFFSET_MIN) / _DUAL_SAFE_OFFSET_SPAN
    normalized = max(0.0, min(1.0, normalized))
    target_y = _DUAL_SAFE_Y_LOW + normalized * (_DUAL_SAFE_Y_HIGH - _DUAL_SAFE_Y_LOW)

    low_l, high_l = 0.0, 1.0
    best_l = 0.5
    for _ in range(20):
        mid_l = (low_l + high_l) / 2.0
        r, g, b = _oklch_to_rgb_clamped(mid_l, target_chroma, h)
        y = relative_luminance(r, g, b)
        if y >= target_y:
            best_l = mid_l
            high_l = mid_l
        else:
            low_l = mid_l

    r, g, b = _oklch_to_rgb_clamped(best_l, target_chroma, h)
    res_hex = rgb_to_hex(r, g, b)
    if contrast_ratio(res_hex, "#000000") >= _CONTRAST_FLOOR and contrast_ratio(res_hex, "#FFFFFF") >= _CONTRAST_FLOOR:
        return res_hex

    # Safety net: the ordered target left the dual-safe band (should not
    # happen given _DUAL_SAFE_Y_LOW/_HIGH's derivation, but a role with an
    # unusually wide gamut-clamping footprint could still miss narrowly).
    # Fall back to the exact band midpoint, which the pre-existing solver
    # already proved dual-safe for every role.
    target_y = (_DUAL_SAFE_Y_LOW + _DUAL_SAFE_Y_HIGH) / 2.0
    low_l, high_l = 0.0, 1.0
    best_l = 0.5
    for _ in range(20):
        mid_l = (low_l + high_l) / 2.0
        r, g, b = _oklch_to_rgb_clamped(mid_l, target_chroma, h)
        y = relative_luminance(r, g, b)
        if y >= target_y:
            best_l = mid_l
            high_l = mid_l
        else:
            low_l = mid_l
    r, g, b = _oklch_to_rgb_clamped(best_l, target_chroma, h)
    res_hex = rgb_to_hex(r, g, b)
    step = 0.005
    while contrast_ratio(res_hex, "#000000") < _CONTRAST_FLOOR and best_l < 1.0:
        best_l = min(1.0, best_l + step)
        r, g, b = _oklch_to_rgb_clamped(best_l, target_chroma, h)
        res_hex = rgb_to_hex(r, g, b)
        step *= 1.2
    return res_hex


#: The dual-safe luminance band: [_DUAL_SAFE_Y_LOW, _DUAL_SAFE_Y_HIGH] is
#: exactly the intersection where a colour clears 4.5:1 against both
#: #000000 and #FFFFFF -- solved from (Y + 0.05) / 0.05 == 4.5 (low) and
#: 1.05 / (Y + 0.05) == 4.5 (high).
_DUAL_SAFE_Y_LOW: Final[float] = 0.175
_DUAL_SAFE_Y_HIGH: Final[float] = 1.05 / _CONTRAST_FLOOR - 0.05
_DUAL_SAFE_OFFSET_MIN: Final[float] = min(a.l_ref - _REFERENCE_BACKGROUND_L for a in ROLE_ANCHORS.values())
_DUAL_SAFE_OFFSET_MAX: Final[float] = max(a.l_ref - _REFERENCE_BACKGROUND_L for a in ROLE_ANCHORS.values())
_DUAL_SAFE_OFFSET_SPAN: Final[float] = max(_DUAL_SAFE_OFFSET_MAX - _DUAL_SAFE_OFFSET_MIN, 1e-9)


def resolve_palette(surface_hex: str | None) -> dict[str, str]:
    """Build full role table for specified surface_hex or dual-safe fallback."""
    return _resolve_palette_cached(surface_hex)


def _resolve_palette_uncached(surface_hex: str | None) -> dict[str, str]:
    table: dict[str, str] = {}
    for role, anchor in ROLE_ANCHORS.items():
        if surface_hex is not None:
            table[role] = solve_for_surface(anchor, surface_hex)
        else:
            table[role] = solve_dual_safe(anchor)
    return table


# Call form (rather than decorator form) keeps mypy's disallow_any_explicit /
# disallow_any_decorated settings clean without a type: ignore suppression --
# the same first-party idiom used by ralph.display.language_inference._cached_infer.
_resolve_palette_cached = functools.lru_cache(maxsize=8)(_resolve_palette_uncached)


def derive_preview_background(surface_hex: str) -> str:
    """Derive an owned preview surface fill from the given surface hex."""
    r, g, b = hex_to_rgb(surface_hex)
    lab_l, a, b_lab = rgb_to_oklab(r, g, b)
    is_light = relative_luminance(r, g, b) > _LIGHT_BG_LUMINANCE_CROSSOVER
    new_l = lab_l - 0.025 if is_light else lab_l + 0.035
    new_l = max(0.0, min(1.0, new_l))

    pr, pg, pb = oklab_to_rgb(new_l, a, b_lab)
    return rgb_to_hex(pr, pg, pb)
