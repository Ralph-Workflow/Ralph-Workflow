from __future__ import annotations

from dataclasses import dataclass

MATCH_STRATEGIES: frozenset[str] = frozenset({"exact", "within_target", "all_in_target"})


@dataclass(frozen=True)
class TextEditAnchor:
    start_line: int
    end_line: int
    match_strategy: str
