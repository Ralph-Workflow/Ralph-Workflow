"""Rich colour-system quantisation helpers for the degraded-depth
accessibility floor (C-5).

Reuses Rich's own colour-downgrade path (``rich.color.Color.downgrade``)
rather than hand-rolling a second cube-distance/ANSI-16 nearest-colour
implementation, so the quantised hex this module returns is exactly what
Rich would actually emit for a given ``color_system`` -- not merely an
independent approximation of it. Kept as a small sibling module rather than
folded into :mod:`ralph.display._palette` so that module's solver stays
free of a Rich dependency (see its module docstring / the project's
"no new runtime dependency for colour maths" non-goal -- Rich itself is
already a core dependency everywhere else in ``ralph.display``, so this is
not a new one, but ``_palette`` itself stays self-contained).
"""

from __future__ import annotations

from typing import Final, Literal

from rich.color import Color, ColorSystem

#: The three colour depths C-5 requires verification against.
ColorDepth = Literal["truecolor", "256", "standard"]

_SYSTEM_BY_DEPTH: Final[dict[str, ColorSystem]] = {
    "256": ColorSystem.EIGHT_BIT,
    "standard": ColorSystem.STANDARD,
}


def quantise_hex(hex_str: str, depth: ColorDepth) -> str:
    """Return the hex Rich would actually render ``hex_str`` as at ``depth``.

    ``depth="truecolor"`` is a no-op (the input hex passes through
    unchanged, upper-cased) so callers can loop over all three depths
    uniformly.
    """
    if depth == "truecolor":
        return hex_str.upper()
    system = _SYSTEM_BY_DEPTH[depth]
    color = Color.parse(hex_str).downgrade(system)
    triplet = color.get_truecolor()
    return f"#{triplet.red:02X}{triplet.green:02X}{triplet.blue:02X}"
