"""A line-span anchor constraining where an edit may match."""

from __future__ import annotations

from dataclasses import dataclass

#: Match strategies accepted by :class:`TextEditAnchor`.
MATCH_STRATEGIES: frozenset[str] = frozenset({"exact", "within_target", "all_in_target"})


@dataclass(frozen=True)
class TextEditAnchor:
    """A 1-based inclusive line span constraining where edits may match."""

    start_line: int
    end_line: int
    match_strategy: str
