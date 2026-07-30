"""Utilities for normalizing VT/TUI output into stable semantic text.

The goal is not pixel-perfect replay. It is to collapse common terminal repaint noise
into a transcript surface that downstream Claude-interactive parsers can reason about
without being tightly coupled to one specific TUI paint pattern.
"""

from __future__ import annotations

import re

# ECMA-48/ECMA-35 escape sequences plus the SO/SI (\x0E/\x0F) charset-shift
# control bytes. The generic ESC forms (intermediates 0x20-0x2F followed by a
# final 0x30-0x7E, or a single final byte) cover charset designations like
# ESC(B and cursor save/restore ESC7/ESC8, which Claude Code >= 2.1.x emits
# between repaints and which otherwise survive into parsed agent text.
_ANSI_ESCAPE_RE = re.compile(
    r"\x1B(?:\[[0-?]*[ -/]*[@-~]"  # CSI sequences
    r"|\][^\x1b\x07]*(?:\x07|\x1b\\)"  # OSC sequences
    r"|[ -/]+[0-~]"  # ESC + intermediates + final (charset designation, ...)
    r"|[0-~])"  # two-byte ESC sequences (DECSC/DECRC, keypad modes, ...)
    r"|[\x0E\x0F]"  # SO/SI charset-shift control bytes
)


def normalize_vt_text(raw: str) -> str:
    """Strip ANSI control noise and collapse carriage-return repaints.

    Carriage returns are treated as "rewrite the current line" markers instead of
    semantic newlines so spinner updates and partial repaints do not create duplicate
    transcript entries.

    A lone ``\\r`` clears the current accumulated line (rewrite). However, ``\\r\\r\\n``
    or ``\\r\\n`` at content boundaries is treated as a line break, not a double rewrite,
    to avoid discarding menu prompts and other multi-line TUI content that uses CR as a
    cheap line separator.
    """

    ansi_free = _ANSI_ESCAPE_RE.sub("", raw)
    current_line = ""
    output: list[str] = []
    index = 0
    length = len(ansi_free)

    while index < length:
        char = ansi_free[index]
        if char == "\r":
            if index + 1 < length and ansi_free[index + 1] in ("\r", "\n"):
                lookahead = ansi_free[index + 1]
                if lookahead == "\n":
                    output.append(f"{current_line}\n")
                    current_line = ""
                    index += 2
                    continue
                if lookahead == "\r" and index + 2 < length and ansi_free[index + 2] == "\n":
                    output.append(f"{current_line}\n")
                    current_line = ""
                    index += 3
                    continue
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
