"""Predictable head+tail condensation for oversized content lines."""

from __future__ import annotations

import os
from typing import Literal, overload

from rich.cells import cell_len

from ralph.display.long_content_summary import (
    build_ai_summary,
    build_headline_or_placeholder,
    should_summarize,
)

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


@overload
def condense_content(
    text: str,
    *,
    soft_limit: int = ...,
    hard_limit: int = ...,
    overflow_ref: str | None = ...,
    summary: Literal[False] = ...,
) -> tuple[str, bool]: ...


@overload
def condense_content(
    text: str,
    *,
    soft_limit: int = ...,
    hard_limit: int = ...,
    overflow_ref: str | None = ...,
    summary: Literal[True],
) -> tuple[str, bool, str | None, str | None]: ...


def condense_content(  # noqa: PLR0911, PLR0912
    text: str,
    *,
    soft_limit: int = _SOFT_LIMIT,
    hard_limit: int = _HARD_LIMIT,
    overflow_ref: str | None = None,
    summary: bool = False,
) -> tuple[str, bool] | tuple[str, bool, str | None, str | None]:
    """Condense *text* so it fits within display limits.

    Returns ``(visible, condensed_flag)`` when *summary* is False (default).
    Returns ``(visible, condensed_flag, summary_line, ai_summary_line)`` when
    *summary* is True, where ``summary_line`` is a non-None headline string only
    when should_summarize() returns True for the content, and ``ai_summary_line``
    is a non-None AI-generated summary only when the AI hook is configured and
    RALPH_LONG_CONTENT_AI_SUMMARY=1.

    Truncation suffixes use parentheses ``(...)`` rather than brackets to avoid
    being misinterpreted as Rich markup tags by downstream renderers.

    When *overflow_ref* is provided it is embedded in the truncation suffix so
    direct callers (e.g. tests) can see the reference inline. When it is None
    the suffix is simply ``(truncated)`` — the caller is expected to surface the
    reference via ``PlainLogRenderer.emit_activity_line(condensed_ref=...)``.

    Rules:
    - If ``cell_len(text) <= soft_limit``: return ``(text, False[, None, None])``
    - If ``cell_len(text) <= hard_limit``: head-only truncation with suffix
    - If ``cell_len(text) > hard_limit``: head + tail with middle elided
    """
    if not text:
        if summary:
            return ("", False, None, None)
        return ("", False)

    try:
        total = cell_len(text)
    except Exception:
        if summary:
            return (text, False, None, None)
        return (text, False)

    if total <= soft_limit:
        if summary:
            return (text, False, None, None)
        return (text, False)

    if total <= hard_limit:
        head = _slice_to_cells(text, soft_limit)
        # Use (...) not [...] to avoid Rich treating this as a markup tag
        if overflow_ref is not None:
            suffix = f" … (truncated, see {overflow_ref})"
        else:
            suffix = " … (truncated)"
        visible = head + suffix
        if summary:
            if should_summarize(text, os.environ):
                summary_line: str | None = build_headline_or_placeholder(text, max_chars=200)
                ai_summary_line: str | None = build_ai_summary(text, os.environ)
            else:
                summary_line = None
                ai_summary_line = None
            return (visible, True, summary_line, ai_summary_line)
        return (visible, True)

    # hard_limit exceeded: head + tail
    head_cells = hard_limit // 2
    tail_cells = hard_limit - head_cells

    head = _slice_to_cells(text, head_cells)
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
    if overflow_ref is not None:
        middle = f" … (+{omitted} chars, see {overflow_ref}) … "
    else:
        middle = f" … (+{omitted} chars truncated) … "
    visible = head + middle + tail
    if summary:
        if should_summarize(text, os.environ):
            summary_line = build_headline_or_placeholder(text, max_chars=200)
            ai_summary_line = build_ai_summary(text, os.environ)
        else:
            summary_line = None
            ai_summary_line = None
        return (visible, True, summary_line, ai_summary_line)
    return (visible, True)


__all__ = ["condense_content"]
