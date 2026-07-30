"""Sequential first-occurrence application of text edits."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.tools.text_edits._applied import AppliedTextEdits
from ralph.mcp.tools.text_edits._diff import unified_text_diff
from ralph.mcp.tools.text_edits._rejected import RejectedTextEdits

if TYPE_CHECKING:
    from ralph.mcp.tools.text_edits._anchor import TextEditAnchor
    from ralph.mcp.tools.text_edits._text_edit import TextEdit


def line_range_to_byte_offsets(text: str, start_line: int, end_line: int) -> tuple[int, int]:
    """Convert ``(start_line, end_line)`` (1-based, inclusive) to byte offsets.

    The mapping is conservative: missing line boundaries fall back to ``0``
    and ``len(text)`` so anchored matching never crashes on edge cases.
    """
    if not text:
        return 0, 0
    lines = text.splitlines(keepends=True)
    if not lines:
        return 0, 0
    start_index = max(0, min(len(lines), start_line - 1))
    end_index = max(start_index, min(len(lines), end_line))
    start_offset = sum(len(line) for line in lines[:start_index])
    end_offset = sum(len(line) for line in lines[:end_index])
    return start_offset, end_offset


def apply_text_edits(
    original: str,
    edits: list[TextEdit],
    *,
    label: str,
    anchor: TextEditAnchor | None = None,
) -> AppliedTextEdits | RejectedTextEdits:
    """Apply ``edits`` sequentially to ``original``.

    Returns :class:`AppliedTextEdits` when every edit matched, otherwise
    :class:`RejectedTextEdits` carrying the structured rejection payload.
    The caller persists nothing on rejection — the batch is atomic.
    """
    current = original
    applied: list[TextEdit] = []

    for index, edit in enumerate(edits):
        match_index = current.find(edit.old_text)
        if match_index == -1:
            return RejectedTextEdits(
                payload={
                    "status": "no_match",
                    "edit_index": index,
                    "preview": unified_text_diff(original, current, label=label),
                }
            )
        if anchor is not None:
            rejection = _check_anchor(current, edit, index, anchor, match_index)
            if rejection is not None:
                return rejection
        current = (
            current[:match_index] + edit.new_text + current[match_index + len(edit.old_text) :]
        )
        applied.append(edit)

    return AppliedTextEdits(original=original, content=current, applied=tuple(applied), label=label)


def _check_anchor(
    current: str,
    edit: TextEdit,
    index: int,
    anchor: TextEditAnchor,
    match_index: int,
) -> RejectedTextEdits | None:
    """Return a rejection when ``edit`` violates the anchor, else ``None``."""
    anchor_start, anchor_end = line_range_to_byte_offsets(
        current, anchor.start_line, anchor.end_line
    )
    old_length = len(edit.old_text)
    match_end = match_index + old_length

    if anchor.match_strategy == "exact":
        if match_index != anchor_start or match_end != anchor_end:
            return _anchor_rejection("match_strategy_exact_violation", index)
        return None

    if anchor.match_strategy == "within_target":
        if match_index < anchor_start or match_end > anchor_end:
            return _anchor_rejection("match_strategy_within_target_violation", index)
        return None

    if anchor.match_strategy == "all_in_target":
        return _check_all_in_target(current, edit, index, (anchor_start, anchor_end), match_index)

    return None


def _check_all_in_target(
    current: str,
    edit: TextEdit,
    index: int,
    span: tuple[int, int],
    match_index: int,
) -> RejectedTextEdits | None:
    """Reject unless every ``old_text`` occurrence lies inside ``span``.

    The boundary is checked against ``old_text`` (NOT ``new_text``) so a
    caller cannot hide an over-broad oldText behind a short replacement.
    """
    anchor_start, anchor_end = span
    old_length = len(edit.old_text)
    occurrence_starts = _occurrence_starts(current, edit.old_text)
    if not occurrence_starts:
        return RejectedTextEdits(payload={"status": "no_match", "edit_index": index})
    outside = any(
        occurrence < anchor_start or (occurrence + old_length) > anchor_end
        for occurrence in occurrence_starts
    )
    if not outside:
        return None
    return RejectedTextEdits(
        payload={
            "status": "ambiguous_target",
            "reason": "match_strategy_all_in_target_violation",
            "edit_index": index,
            "first_match_index": match_index,
            "first_match_in_target": (
                anchor_start <= match_index and (match_index + old_length) <= anchor_end
            ),
        }
    )


def _occurrence_starts(text: str, needle: str) -> list[int]:
    """Return the start offsets of every non-overlapping ``needle`` occurrence."""
    starts: list[int] = []
    search_from = 0
    while True:
        found = text.find(needle, search_from)
        if found == -1:
            return starts
        starts.append(found)
        search_from = found + len(needle)


def _anchor_rejection(reason: str, index: int) -> RejectedTextEdits:
    """Return the standard ``ambiguous_target`` rejection for anchor violations."""
    return RejectedTextEdits(
        payload={"status": "ambiguous_target", "reason": reason, "edit_index": index}
    )
