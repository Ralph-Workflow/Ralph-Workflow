"""Predictable head+tail condensation for oversized content lines."""

from __future__ import annotations

from rich.cells import cell_len

_SOFT_LIMIT = 400
_HARD_LIMIT = 4000


def _slice_to_cells(text: str, max_cells: int) -> str:
    """Return the longest prefix of *text* that fits within *max_cells* display cells."""
    result: list[str] = []
    used = 0
    for char in text:
        w = cell_len(char)
        if used + w > max_cells:
            break
        result.append(char)
        used += w
    return "".join(result)


def condense_content(
    text: str,
    *,
    soft_limit: int = _SOFT_LIMIT,
    hard_limit: int = _HARD_LIMIT,
    overflow_ref: str | None = None,
) -> tuple[str, bool]:
    """Condense *text* so it fits within display limits.

    Returns ``(visible, condensed_flag)`` where ``condensed_flag`` is ``True``
    when any truncation occurred.

    Rules:
    - If ``cell_len(text) <= soft_limit``: return ``(text, False)``
    - If ``cell_len(text) <= hard_limit``: head-only truncation with suffix
    - If ``cell_len(text) > hard_limit``: head + tail with middle elided
    """
    if not text:
        return ("", False)

    ref = overflow_ref if overflow_ref is not None else "raw unavailable"

    try:
        total = cell_len(text)
    except Exception:
        return (text, False)

    if total <= soft_limit:
        return (text, False)

    if total <= hard_limit:
        head = _slice_to_cells(text, soft_limit)
        suffix = f" … [truncated, see {ref}]"
        return (head + suffix, True)

    # hard_limit exceeded: head + tail
    head_cells = hard_limit // 2
    tail_cells = hard_limit - head_cells

    head = _slice_to_cells(text, head_cells)
    # Build tail from the end
    tail_chars: list[str] = []
    used = 0
    for char in reversed(text):
        w = cell_len(char)
        if used + w > tail_cells:
            break
        tail_chars.append(char)
        used += w
    tail = "".join(reversed(tail_chars))

    omitted = total - cell_len(head) - cell_len(tail)
    middle = f" … [+{omitted} chars, see {ref}] … "
    return (head + middle + tail, True)


__all__ = ["condense_content"]
