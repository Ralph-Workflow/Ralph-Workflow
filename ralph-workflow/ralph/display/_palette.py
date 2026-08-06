"""Deterministic Monokai Pro-derived palette generator with WCAG 4.5:1 contrast solver."""

from __future__ import annotations

import functools
import math
from typing import Final, NamedTuple


class RoleAnchor(NamedTuple):
    """Semantic role anchor holding OKLCh hue and target chroma."""

    hue: float
    chroma: float


ROLE_ANCHORS: Final[dict[str, RoleAnchor]] = {
    "success": RoleAnchor(hue=130.7, chroma=0.142),
    "error": RoleAnchor(hue=8.5, chroma=0.194),
    "warning": RoleAnchor(hue=46.2, chroma=0.136),
    "skipped": RoleAnchor(hue=90.5, chroma=0.139),
    "info": RoleAnchor(hue=205.7, chroma=0.095),
    "running": RoleAnchor(hue=205.7, chroma=0.095),
    "pending": RoleAnchor(hue=290.7, chroma=0.121),
    "analysis": RoleAnchor(hue=290.7, chroma=0.121),
    "chrome": RoleAnchor(hue=205.7, chroma=0.095),
    "agent_text": RoleAnchor(hue=205.7, chroma=0.095),
    "elision": RoleAnchor(hue=290.7, chroma=0.121),
    "muted": RoleAnchor(hue=205.7, chroma=0.060),
    "diff_added": RoleAnchor(hue=130.7, chroma=0.142),
    "diff_removed": RoleAnchor(hue=8.5, chroma=0.194),
}

_CONTRAST_FLOOR: Final[float] = 4.5
_SRGB_THRESHOLD: Final[float] = 0.04045
_LINEAR_THRESHOLD: Final[float] = 0.0031308
_SHORT_HEX_LEN: Final[int] = 3
_LIGHT_BG_LUMINANCE_CROSSOVER: Final[float] = 0.4


# --- Color Space Conversions ---


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


def solve_for_surface(
    anchor: RoleAnchor, surface_hex: str, min_ratio: float = _CONTRAST_FLOOR
) -> str:
    """Hold hue, pick optimal polarity, binary search L for min_ratio contrast, clamp C in gamut."""
    surf_r, surf_g, surf_b = hex_to_rgb(surface_hex)
    y_surf = relative_luminance(surf_r, surf_g, surf_b)

    max_upper_ratio = 1.05 / (y_surf + 0.05)
    max_lower_ratio = (y_surf + 0.05) / 0.05

    lighter = max_upper_ratio >= min_ratio and (
        max_upper_ratio >= max_lower_ratio or max_lower_ratio < min_ratio
    )

    h = anchor.hue
    target_chroma = anchor.chroma

    if lighter:
        target_y = min_ratio * (y_surf + 0.05) - 0.05
        target_y = min(1.0, max(0.0, target_y))

        low_l, high_l = 0.0, 1.0
        best_l = 1.0
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
        # Ensure contrast floor after chroma clamping
        step = 0.005
        while contrast_ratio(res_hex, surface_hex) < min_ratio and best_l < 1.0:
            best_l = min(1.0, best_l + step)
            r, g, b = _oklch_to_rgb_clamped(best_l, target_chroma, h)
            res_hex = rgb_to_hex(r, g, b)
            step *= 1.2
        return res_hex
    else:
        target_y = (y_surf + 0.05) / min_ratio - 0.05
        target_y = max(0.0, min(1.0, target_y))

        low_l, high_l = 0.0, 1.0
        best_l = 0.0
        for _ in range(20):
            mid_l = (low_l + high_l) / 2.0
            r, g, b = _oklch_to_rgb_clamped(mid_l, target_chroma, h)
            y = relative_luminance(r, g, b)
            if y <= target_y:
                best_l = mid_l
                low_l = mid_l
            else:
                high_l = mid_l

        r, g, b = _oklch_to_rgb_clamped(best_l, target_chroma, h)
        res_hex = rgb_to_hex(r, g, b)
        # Ensure contrast floor after chroma clamping
        step = 0.005
        while contrast_ratio(res_hex, surface_hex) < min_ratio and best_l > 0.0:
            best_l = max(0.0, best_l - step)
            r, g, b = _oklch_to_rgb_clamped(best_l, target_chroma, h)
            res_hex = rgb_to_hex(r, g, b)
            step *= 1.2
        return res_hex


def solve_dual_safe(anchor: RoleAnchor) -> str:
    """Target the [0.175, 0.1833] dual-safe luminance band for undetermined surfaces."""
    target_y = (0.175 + 0.1833) / 2.0
    h = anchor.hue
    target_chroma = anchor.chroma

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


def resolve_palette(surface_hex: str | None) -> dict[str, str]:
    """Build full role table for specified surface_hex or dual-safe fallback."""
    return _resolve_palette_cached(surface_hex)


@functools.lru_cache(maxsize=8)  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
def _resolve_palette_cached(surface_hex: str | None) -> dict[str, str]:

    table: dict[str, str] = {}
    for role, anchor in ROLE_ANCHORS.items():
        if surface_hex is not None:
            table[role] = solve_for_surface(anchor, surface_hex)
        else:
            table[role] = solve_dual_safe(anchor)
    return table



def derive_preview_background(surface_hex: str) -> str:
    """Derive an owned preview surface fill from the given surface hex."""
    r, g, b = hex_to_rgb(surface_hex)
    lab_l, a, b_lab = rgb_to_oklab(r, g, b)
    is_light = relative_luminance(r, g, b) > _LIGHT_BG_LUMINANCE_CROSSOVER
    new_l = lab_l - 0.025 if is_light else lab_l + 0.035
    new_l = max(0.0, min(1.0, new_l))

    pr, pg, pb = oklab_to_rgb(new_l, a, b_lab)
    return rgb_to_hex(pr, pg, pb)

