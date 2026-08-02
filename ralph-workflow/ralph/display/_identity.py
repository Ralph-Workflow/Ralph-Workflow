"""Color-identity collision helpers for the display theme."""

from __future__ import annotations

import re
import zlib
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

_IDENTITY_WS_RE: Final[re.Pattern[str]] = re.compile(r"[\s_]+")
_DEUTERANOPIA_MATRIX: Final[tuple[tuple[float, float, float], ...]] = (
    (0.625, 0.375, 0.0), (0.7, 0.3, 0.0), (0.0, 0.3, 0.7)
)
_PROTANOPIA_MATRIX: Final[tuple[tuple[float, float, float], ...]] = (
    (0.567, 0.433, 0.0), (0.558, 0.442, 0.0), (0.0, 0.242, 0.758)
)
_TRITANOPIA_MATRIX: Final[tuple[tuple[float, float, float], ...]] = (
    (0.95, 0.05, 0.0), (0.0, 0.433, 0.567), (0.0, 0.475, 0.525)
)
_CVD_MATRICES: Final[tuple[tuple[tuple[float, float, float], ...], ...]] = (
    _DEUTERANOPIA_MATRIX, _PROTANOPIA_MATRIX, _TRITANOPIA_MATRIX
)


def normalize_identity_name(name: str) -> str:
    """Return a stable, lowercase, separator- and case-folded identity."""
    if not name:
        return "unknown"
    folded = _IDENTITY_WS_RE.sub("-", name.strip().lower()).strip("-")
    return folded or "unknown"


def identity_slot(name: str, palette_size: int) -> int:
    """Return the deterministic palette slot for ``name``."""
    return zlib.crc32(normalize_identity_name(name).encode("utf-8")) % palette_size


def simulate_cvd(
    hex_color: str,
    matrix: tuple[tuple[float, float, float], ...],
    *,
    rgb: Callable[[str], tuple[int, int, int]] | None = None,
) -> str:
    """Return the simulated CVD colour for a hex input."""
    raw = hex_color.lstrip("#")
    parsed_rgb = tuple(int(raw[index : index + 2], 16) for index in range(0, 6, 2))
    source_rgb = rgb(hex_color) if rgb is not None else parsed_rgb
    red, green, blue = (channel / 255.0 for channel in source_rgb)
    channels = (
        matrix[0][0] * red + matrix[0][1] * green + matrix[0][2] * blue,
        matrix[1][0] * red + matrix[1][1] * green + matrix[1][2] * blue,
        matrix[2][0] * red + matrix[2][1] * green + matrix[2][2] * blue,
    )
    return "#" + "".join(f"{max(0, min(255, int(channel * 255))):02X}" for channel in channels)


def identity_color(
    name: str,
    *,
    palette: tuple[str, ...],
    active: Iterable[str] | None,
    rgb: Callable[[str], tuple[int, int, int]],
) -> str:
    """Resolve an identity colour without collision under documented CVD simulations."""
    base_slot = identity_slot(name, len(palette))
    if active is None:
        return palette[base_slot]

    def simulate(hex_color: str, matrix: tuple[tuple[float, float, float], ...]) -> str:
        return simulate_cvd(hex_color, matrix, rgb=rgb)

    resolved: dict[str, str] = {}
    for other in sorted(frozenset(active)):
        base = identity_slot(other, len(palette))
        occupied = set(resolved.values())
        occupied_cvd = {simulate(color, matrix) for color in occupied for matrix in _CVD_MATRICES}
        for offset in range(len(palette)):
            candidate = palette[(base + offset) % len(palette)]
            if candidate not in occupied and not ({simulate(candidate, matrix) for matrix in _CVD_MATRICES} & occupied_cvd):
                resolved[other] = candidate
                break
        else:
            resolved[other] = next(color for color in palette if color not in occupied)
    if name in resolved:
        return resolved[name]
    active_hexes = set(resolved.values())
    active_cvd = {simulate(color, matrix) for color in active_hexes for matrix in _CVD_MATRICES}
    for offset in range(len(palette)):
        candidate = palette[(base_slot + offset) % len(palette)]
        if candidate not in active_hexes and not ({simulate(candidate, matrix) for matrix in _CVD_MATRICES} & active_cvd):
            return candidate
    return palette[base_slot]
