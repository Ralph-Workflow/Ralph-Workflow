
from __future__ import annotations

from typing import Final, Literal

from rich.color import Color, ColorSystem

ColorDepth = Literal["truecolor", "256", "standard"]

_SYSTEM_BY_DEPTH: Final[dict[str, ColorSystem]] = {
    "256": ColorSystem.EIGHT_BIT,
    "standard": ColorSystem.STANDARD,
}


def quantise_hex(hex_str: str, depth: ColorDepth) -> str:
    if depth == "truecolor":
        return hex_str.upper()
    system = _SYSTEM_BY_DEPTH[depth]
    color = Color.parse(hex_str).downgrade(system)
    triplet = color.get_truecolor()
    return f"#{triplet.red:02X}{triplet.green:02X}{triplet.blue:02X}"
