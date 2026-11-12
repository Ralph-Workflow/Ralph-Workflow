from __future__ import annotations

import re

_ANSI_ESCAPE_RE = re.compile(
    r"\x1B(?:\[[0-?]*[ -/]*[@-~]"
    r"|\][^\x1b\x07]*(?:\x07|\x1b\\)"
    r"|[ -/]+[0-~]"
    r"|[0-~])"
    r"|[\x0E\x0F]"
)


_REWRITE_CONTROLS = re.compile(r"[\r\n\b]")


def normalize_vt_text(raw: str) -> str:

    ansi_free = _ANSI_ESCAPE_RE.sub("", raw)
    if _REWRITE_CONTROLS.search(ansi_free) is None:
        return ansi_free
    current_line: list[str] = []
    output: list[str] = []
    index = 0
    length = len(ansi_free)

    while index < length:
        char = ansi_free[index]
        if char == "\r":
            if index + 1 < length and ansi_free[index + 1] in ("\r", "\n"):
                lookahead = ansi_free[index + 1]
                if lookahead == "\n":
                    output.append("".join(current_line) + "\n")
                    current_line = []
                    index += 2
                    continue
                if lookahead == "\r" and index + 2 < length and ansi_free[index + 2] == "\n":
                    output.append("".join(current_line) + "\n")
                    current_line = []
                    index += 3
                    continue
            current_line = []
            index += 1
            continue
        if char == "\n":
            output.append("".join(current_line) + "\n")
            current_line = []
            index += 1
            continue
        if char == "\b":
            if current_line:
                current_line.pop()
            index += 1
            continue
        current_line.append(char)
        index += 1

    if current_line:
        output.append("".join(current_line))
    return "".join(output)


__all__ = ["normalize_vt_text"]
