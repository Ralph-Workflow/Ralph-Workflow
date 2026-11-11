
from __future__ import annotations

import bisect
import functools
import math
from typing import Final, NamedTuple

from ralph.display._frequency_tier import FrequencyTier


class RoleAnchor(NamedTuple):

    hue: float
    chroma: float
    l_ref: float



_SRGB_THRESHOLD: Final[float] = 0.04045
_LINEAR_THRESHOLD: Final[float] = 0.0031308
_SHORT_HEX_LEN: Final[int] = 3


def srgb_to_linear(c: float) -> float:
    if c <= _SRGB_THRESHOLD:
        return c / 12.92
    return math.pow((c + 0.055) / 1.055, 2.4)


def linear_to_srgb(c: float) -> float:
    if c <= _LINEAR_THRESHOLD:
        return 12.92 * c
    return 1.055 * math.pow(c, 1.0 / 2.4) - 0.055


def hex_to_rgb(hex_str: str) -> tuple[float, float, float]:
    cleaned = hex_str.strip().lstrip("#")
    if len(cleaned) == _SHORT_HEX_LEN:
        cleaned = "".join(ch * 2 for ch in cleaned)
    r = int(cleaned[0:2], 16) / 255.0
    g = int(cleaned[2:4], 16) / 255.0
    b = int(cleaned[4:6], 16) / 255.0
    return r, g, b


def rgb_to_hex(r: float, g: float, b: float) -> str:
    r_int = max(0, min(255, round(r * 255.0)))
    g_int = max(0, min(255, round(g * 255.0)))
    b_int = max(0, min(255, round(b * 255.0)))
    return f"#{r_int:02X}{g_int:02X}{b_int:02X}"


def rgb_to_oklab(r: float, g: float, b: float) -> tuple[float, float, float]:
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
    chroma = math.hypot(a, b_lab)
    h = math.degrees(math.atan2(b_lab, a)) % 360.0
    return l_val, chroma, h


def oklch_to_oklab(l_val: float, c_val: float, h: float) -> tuple[float, float, float]:
    rad = math.radians(h)
    a = c_val * math.cos(rad)
    b_lab = c_val * math.sin(rad)
    return l_val, a, b_lab


def is_in_gamut(r: float, g: float, b: float, tol: float = 1e-4) -> bool:
    return (-tol <= r <= 1.0 + tol) and (-tol <= g <= 1.0 + tol) and (-tol <= b <= 1.0 + tol)


def relative_luminance(r: float, g: float, b: float) -> float:
    lr = srgb_to_linear(r)
    lg = srgb_to_linear(g)
    lb = srgb_to_linear(b)
    return 0.2126 * lr + 0.7152 * lg + 0.0722 * lb


def contrast_ratio(hex_a: str, hex_b: str) -> float:
    y_a = relative_luminance(*hex_to_rgb(hex_a))
    y_b = relative_luminance(*hex_to_rgb(hex_b))
    lighter = max(y_a, y_b)
    darker = min(y_a, y_b)
    return (lighter + 0.05) / (darker + 0.05)


def _oklab_l_of_hex(hex_str: str) -> float:
    lab_l, _a, _b = rgb_to_oklab(*hex_to_rgb(hex_str))
    return lab_l


def oklch_of_hex(hex_str: str) -> tuple[float, float, float]:
    return oklab_to_oklch(*rgb_to_oklab(*hex_to_rgb(hex_str)))


def _anchor_of_hex(hex_str: str) -> RoleAnchor:
    l_val, chroma, hue = oklch_of_hex(hex_str)
    return RoleAnchor(hue=hue, chroma=chroma, l_ref=l_val)


_CANONICAL_DARK_SURFACE_HEX: Final[str] = "#2D2A2E"
_CANONICAL_LIGHT_SURFACE_HEX: Final[str] = "#FAF8F5"
_LIGHT_BG_LUMINANCE_CROSSOVER: Final[float] = 0.1791

REFERENCE_BACKGROUND_L: Final[float] = _oklab_l_of_hex(_CANONICAL_DARK_SURFACE_HEX)


_success_anchor: Final[RoleAnchor] = _anchor_of_hex("#A9DC76")
_error_anchor: Final[RoleAnchor] = _anchor_of_hex("#FF6188")
_warning_anchor: Final[RoleAnchor] = _anchor_of_hex("#FC9867")
_skipped_anchor: Final[RoleAnchor] = _anchor_of_hex("#FFD866")
_info_anchor: Final[RoleAnchor] = _anchor_of_hex("#78DCE8")
_pending_anchor: Final[RoleAnchor] = _anchor_of_hex("#AB9DF2")
_agent_text_anchor: Final[RoleAnchor] = _anchor_of_hex("#FCFCFA")
_comment_anchor: Final[RoleAnchor] = _anchor_of_hex("#727072")


TIER_1_CHROMA_BUDGET: Final[float] = 0.04
TIER_2_CHROMA_BUDGET: Final[float] = 0.08

ROLE_FREQUENCY_TIER: Final[dict[str, FrequencyTier]] = {
    "foreground": FrequencyTier.FIELD,
    "agent_text": FrequencyTier.FIELD,
    "muted": FrequencyTier.FIELD,
    "comment": FrequencyTier.FIELD,
    "chrome": FrequencyTier.STRUCTURE,
    "elision": FrequencyTier.STRUCTURE,
    "success": FrequencyTier.EVENT,
    "warning": FrequencyTier.EVENT,
    "skipped": FrequencyTier.EVENT,
    "pending": FrequencyTier.EVENT,
    "running": FrequencyTier.EVENT,
    "info": FrequencyTier.EVENT,
    "analysis": FrequencyTier.EVENT,
    "diff_added": FrequencyTier.EVENT,
    "diff_removed": FrequencyTier.EVENT,
    "error": FrequencyTier.ALARM,
}

_MUTED_CHROMA_FRACTION: Final[float] = 0.6
_muted_anchor: Final[RoleAnchor] = RoleAnchor(
    hue=_info_anchor.hue,
    chroma=_comment_anchor.chroma
    + _MUTED_CHROMA_FRACTION * (_info_anchor.chroma - _comment_anchor.chroma),
    l_ref=(REFERENCE_BACKGROUND_L + _comment_anchor.l_ref) / 2.0,
)

_CHROME_CHROMA_FRACTION_OF_TIER_2_BUDGET: Final[float] = 0.75
_chrome_anchor: Final[RoleAnchor] = RoleAnchor(
    hue=_info_anchor.hue,
    chroma=TIER_2_CHROMA_BUDGET * _CHROME_CHROMA_FRACTION_OF_TIER_2_BUDGET,
    l_ref=_info_anchor.l_ref,
)

_RUNNING_L_REF_NUDGE: Final[float] = 0.06
_running_anchor: Final[RoleAnchor] = RoleAnchor(
    hue=_info_anchor.hue,
    chroma=_info_anchor.chroma,
    l_ref=_info_anchor.l_ref - _RUNNING_L_REF_NUDGE,
)

_ANALYSIS_L_REF_NUDGE: Final[float] = 0.12
_ELISION_L_REF_NUDGE: Final[float] = 0.10
_analysis_anchor: Final[RoleAnchor] = RoleAnchor(
    hue=_pending_anchor.hue,
    chroma=_pending_anchor.chroma,
    l_ref=_pending_anchor.l_ref - _ANALYSIS_L_REF_NUDGE,
)
_elision_anchor: Final[RoleAnchor] = RoleAnchor(
    hue=_pending_anchor.hue,
    chroma=_pending_anchor.chroma,
    l_ref=_pending_anchor.l_ref + _ELISION_L_REF_NUDGE,
)

ROLE_ANCHORS: Final[dict[str, RoleAnchor]] = {
    "success": _success_anchor,
    "error": _error_anchor,
    "warning": _warning_anchor,
    "skipped": _skipped_anchor,
    "info": _info_anchor,
    "running": _running_anchor,
    "pending": _pending_anchor,
    "analysis": _analysis_anchor,
    "agent_text": _agent_text_anchor,
    "chrome": _chrome_anchor,
    "elision": _elision_anchor,
    "muted": _muted_anchor,
    "diff_added": _success_anchor,
    "diff_removed": _error_anchor,
    "foreground": _agent_text_anchor,
    "comment": _comment_anchor,
}

_CONTRAST_FLOOR: Final[float] = 4.5
_CHROMA_EPSILON: Final[float] = 1e-9
_SQUEEZE_HEADROOM_THRESHOLD: Final[float] = 0.02

_PURE_BLACK_HEX: Final[str] = "#000000"
_PURE_WHITE_HEX: Final[str] = "#FFFFFF"




def _oklch_to_rgb_clamped(l_val: float, c_val: float, h: float) -> tuple[float, float, float]:
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


def _max_in_gamut_chroma(l_val: float, h: float, upper: float = 0.4) -> float:
    r, g, b = oklab_to_rgb(*oklch_to_oklab(l_val, upper, h))
    if is_in_gamut(r, g, b):
        return upper

    low_c, high_c = 0.0, upper
    best_c = 0.0
    for _ in range(20):
        mid_c = (low_c + high_c) / 2.0
        mr, mg, mb = oklab_to_rgb(*oklch_to_oklab(l_val, mid_c, h))
        if is_in_gamut(mr, mg, mb):
            best_c = mid_c
            low_c = mid_c
        else:
            high_c = mid_c
    return best_c


def _surface_adaptive_chroma(anchor: RoleAnchor, target_l: float) -> float:
    ref_max_chroma = _max_in_gamut_chroma(anchor.l_ref, anchor.hue)
    if ref_max_chroma <= _CHROMA_EPSILON:
        return anchor.chroma
    fraction = min(1.0, anchor.chroma / ref_max_chroma)
    target_max_chroma = _max_in_gamut_chroma(target_l, anchor.hue)
    if target_max_chroma <= _SQUEEZE_HEADROOM_THRESHOLD and anchor.chroma > TIER_1_CHROMA_BUDGET:
        fraction = 1.0
    return fraction * target_max_chroma


def _floor_lightness(
    h: float, target_chroma: float, y_surf: float, min_ratio: float, *, lighter: bool
) -> float:
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


_ROLE_OFFSETS_SORTED: Final[tuple[float, ...]] = tuple(
    sorted(a.l_ref - REFERENCE_BACKGROUND_L for a in ROLE_ANCHORS.values())
)

_COMPRESSION_REACH_ABSOLUTE: Final[float] = 0.12


def _offset_rank(offset: float) -> float:
    lo_idx = bisect.bisect_left(_ROLE_OFFSETS_SORTED, offset)
    hi_idx = bisect.bisect_right(_ROLE_OFFSETS_SORTED, offset) - 1
    rank = (lo_idx + hi_idx) / 2.0
    denom = len(_ROLE_OFFSETS_SORTED) - 1
    return rank / denom if denom > 0 else 0.0


def _nudge_to_floor(
    l_val: float,
    h: float,
    target_chroma: float,
    surface_hex: str,
    min_ratio: float,
    *,
    lighter: bool,
) -> str:
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
    offset = anchor.l_ref - REFERENCE_BACKGROUND_L
    offset_l_raw = surface_l + offset if lighter else surface_l - offset

    floor_l = _floor_lightness(h, target_chroma, y_surf, min_ratio, lighter=lighter)

    band_lo, band_hi = (floor_l, 1.0) if lighter else (0.0, floor_l)
    if band_lo <= offset_l_raw <= band_hi:
        best_l = offset_l_raw
    elif lighter:
        rank = _offset_rank(offset)
        best_l = floor_l + rank * min(_COMPRESSION_REACH_ABSOLUTE, 1.0 - floor_l)
    else:
        rank = _offset_rank(offset)
        best_l = floor_l - rank * min(_COMPRESSION_REACH_ABSOLUTE, floor_l)

    target_chroma = _surface_adaptive_chroma(anchor, best_l)

    return _nudge_to_floor(best_l, h, target_chroma, surface_hex, min_ratio, lighter=lighter)


def solve_dual_safe(anchor: RoleAnchor) -> str:
    h = anchor.hue
    target_chroma = anchor.chroma

    offset = anchor.l_ref - REFERENCE_BACKGROUND_L
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
    if (
        contrast_ratio(res_hex, _PURE_BLACK_HEX) >= _CONTRAST_FLOOR
        and contrast_ratio(res_hex, _PURE_WHITE_HEX) >= _CONTRAST_FLOOR
    ):
        return res_hex

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
    while contrast_ratio(res_hex, _PURE_BLACK_HEX) < _CONTRAST_FLOOR and best_l < 1.0:
        best_l = min(1.0, best_l + step)
        r, g, b = _oklch_to_rgb_clamped(best_l, target_chroma, h)
        res_hex = rgb_to_hex(r, g, b)
        step *= 1.2
    return res_hex


_DUAL_SAFE_Y_LOW: Final[float] = 0.175
_DUAL_SAFE_Y_HIGH: Final[float] = 1.05 / _CONTRAST_FLOOR - 0.05
_DUAL_SAFE_OFFSET_MIN: Final[float] = min(
    a.l_ref - REFERENCE_BACKGROUND_L for a in ROLE_ANCHORS.values()
)
_DUAL_SAFE_OFFSET_MAX: Final[float] = max(
    a.l_ref - REFERENCE_BACKGROUND_L for a in ROLE_ANCHORS.values()
)
_DUAL_SAFE_OFFSET_SPAN: Final[float] = max(_DUAL_SAFE_OFFSET_MAX - _DUAL_SAFE_OFFSET_MIN, 1e-9)


def resolve_palette(surface_hex: str | None) -> dict[str, str]:
    return _resolve_palette_cached(surface_hex)


def _resolve_palette_uncached(surface_hex: str | None) -> dict[str, str]:
    table: dict[str, str] = {}
    for role, anchor in ROLE_ANCHORS.items():
        if surface_hex is not None:
            table[role] = solve_for_surface(anchor, surface_hex)
        else:
            table[role] = solve_dual_safe(anchor)
    return table


_resolve_palette_cached = functools.lru_cache(maxsize=8)(_resolve_palette_uncached)


def palette_solve_stats() -> tuple[int, int]:
    info = _resolve_palette_cached.cache_info()
    return info.hits, info.misses


def reset_palette_solve_stats() -> None:
    _resolve_palette_cached.cache_clear()


def derive_preview_background(surface_hex: str) -> str:
    r, g, b = hex_to_rgb(surface_hex)
    lab_l, a, b_lab = rgb_to_oklab(r, g, b)
    is_light = relative_luminance(r, g, b) > _LIGHT_BG_LUMINANCE_CROSSOVER
    new_l = lab_l - 0.025 if is_light else lab_l + 0.035
    new_l = max(0.0, min(1.0, new_l))

    pr, pg, pb = oklab_to_rgb(new_l, a, b_lab)
    return rgb_to_hex(pr, pg, pb)
