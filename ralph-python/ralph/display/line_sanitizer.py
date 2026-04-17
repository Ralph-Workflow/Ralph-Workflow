"""Line sanitization for safe display in terminal UI.

Handles oversize lines (truncated at max_chars with '…' suffix), binary bytes
(decoded with errors='replace'), CRLF normalization, control character stripping
(tab preserved), and emoji preservation.
"""

import re

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def sanitize_display_line(raw: bytes | str, max_chars: int = 200) -> str:
    """Sanitize a raw agent output line for safe terminal display."""
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = raw

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_CHARS_RE.sub("", text)

    if len(text) > max_chars:
        text = text[:max_chars] + "…"

    return text


__all__ = ["sanitize_display_line"]
