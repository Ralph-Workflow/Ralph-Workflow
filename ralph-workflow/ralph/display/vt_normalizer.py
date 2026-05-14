"""Utilities for normalizing VT/TUI output into stable semantic text.

The goal is not pixel-perfect replay. It is to collapse common terminal repaint noise
into a transcript surface that downstream Claude-interactive parsers can reason about
without being tightly coupled to one specific TUI paint pattern.
"""

from __future__ import annotations

import re

_ANSI_ESCAPE_RE = re.compile(
    r"\x1B(?:\[[0-?]*[ -/]*[@-~]|\][^\x1b\x07]*(?:\x07|\x1b\\)|[@-Z\\-_])"
)


def normalize_vt_text(raw: str) -> str:
    """Strip ANSI control noise and collapse carriage-return repaints.

    Carriage returns are treated as "rewrite the current line" markers instead of
    semantic newlines so spinner updates and partial repaints do not create duplicate
    transcript entries.
    """

    ansi_free = _ANSI_ESCAPE_RE.sub("", raw)
    current_line = ""
    output: list[str] = []
    index = 0
    length = len(ansi_free)

    while index < length:
        char = ansi_free[index]
        if char == "\r":
            current_line = ""
            index += 1
            continue
        if char == "\n":
            output.append(f"{current_line}\n")
            current_line = ""
            index += 1
            continue
        if char == "\b":
            current_line = current_line[:-1]
            index += 1
            continue
        current_line += char
        index += 1

    if current_line:
        output.append(current_line)
    return "".join(output)


__all__ = ["normalize_vt_text"]
