"""Predictable head+tail condensation for oversized content lines."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

from rich.cells import cell_len

from ralph.display.long_content_summary import (
    build_ai_summary,
    build_headline_or_placeholder,
    should_summarize,
)

_SOFT_LIMIT = 400
_HARD_LIMIT = 4000

_CondensedResult = tuple[str, bool] | tuple[str, bool, str | None, str | None]


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


@dataclass
class CondenseOptions:
    """Options for content condensation.

    Attributes:
        soft_limit: Display cell width before showing truncation hint.
        hard_limit: Display cell width before switching to head+tail mode.
        overflow_ref: Reference string embedded in truncation suffix.
        summary: Whether to generate summary lines.
        env: Environment variables mapping for AI summary hooks.
    """

    soft_limit: int = _SOFT_LIMIT
    hard_limit: int = _HARD_LIMIT
    overflow_ref: str | None = None
    summary: bool = False
    env: Mapping[str, str] | None = None


def _build_summaries(
    text: str, env: Mapping[str, str] | None
) -> tuple[str | None, str | None]:
    """Build summary lines if content meets criteria, else return (None, None)."""
    _env = env if env is not None else os.environ
    if should_summarize(text, _env):
        return (
            build_headline_or_placeholder(text, max_chars=200),
            build_ai_summary(text, _env),
        )
    return (None, None)


def _truncation_suffix(overflow_ref: str | None) -> str:
    """Build truncation suffix string."""
    if overflow_ref is not None:
        return f" … (truncated, see {overflow_ref})"
    return " … (truncated)"


def _elision_suffix(omitted: int, overflow_ref: str | None) -> str:
    """Build middle elision suffix string."""
    if overflow_ref is not None:
        return f" … (+{omitted} chars, see {overflow_ref}) … "
    return f" … (+{omitted} chars truncated) … "


def _extract_tail(text: str, tail_cells: int) -> str:
    """Extract tail portion of *text* up to *tail_cells* display cells."""
    tail_chars: list[str] = []
    used = 0
    for char in reversed(text):
        w = cell_len(char)
        if used + w > tail_cells:
            break
        tail_chars.append(char)
        used += w
    return "".join(reversed(tail_chars))


def _return_result(
    visible: str, condensed: bool, options: CondenseOptions, original: str
) -> _CondensedResult:
    """Return result tuple, optionally including summary lines."""
    if options.summary:
        summary_line, ai_summary_line = _build_summaries(original, options.env)
        return (visible, condensed, summary_line, ai_summary_line)
    return (visible, condensed)


def _condense_head_only(text: str, options: CondenseOptions) -> _CondensedResult:
    """Condense using head-only truncation."""
    head = _slice_to_cells(text, options.soft_limit)
    visible = head + _truncation_suffix(options.overflow_ref)
    return _return_result(visible, True, options, text)


def _condense_head_and_tail(
    text: str, total: int, options: CondenseOptions
) -> _CondensedResult:
    """Condense using head + tail with middle elision."""
    head_cells = options.hard_limit // 2
    tail_cells = options.hard_limit - head_cells

    head = _slice_to_cells(text, head_cells)
    tail = _extract_tail(text, tail_cells)

    omitted = total - cell_len(head) - cell_len(tail)
    visible = head + _elision_suffix(omitted, options.overflow_ref) + tail
    return _return_result(visible, True, options, text)


def condense_content(
    text: str,
    *,
    options: CondenseOptions | None = None,
) -> tuple[str, bool] | tuple[str, bool, str | None, str | None]:
    """Condense *text* so it fits within display limits.

    Pass a :class:`CondenseOptions` to configure limits, overflow ref, and
    summarization.  Omit to use defaults.

    Returns ``(visible, condensed_flag)`` when ``options.summary`` is False.
    Returns ``(visible, condensed_flag, summary_line, ai_summary_line)`` when
    ``options.summary`` is True.

    Rules:
    - If ``cell_len(text) <= soft_limit``: return ``(text, False[, None, None])``
    - If ``cell_len(text) <= hard_limit``: head-only truncation with suffix
    - If ``cell_len(text) > hard_limit``: head + tail with middle elided
    """
    opts = options if options is not None else CondenseOptions()
    if not text:
        return _return_result("", False, opts, "")

    try:
        total = cell_len(text)
    except Exception:
        return _return_result(text, False, opts, text)

    if total <= opts.soft_limit:
        return _return_result(text, False, opts, text)

    if total <= opts.hard_limit:
        return _condense_head_only(text, opts)

    return _condense_head_and_tail(text, total, opts)


__all__ = ["CondenseOptions", "condense_content"]
