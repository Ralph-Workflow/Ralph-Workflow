"""One line-anchored body line in a parsed markdown artifact."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedLine:
    """A non-empty content line with its 1-based source line number."""

    line: int
    text: str


__all__ = ["ParsedLine"]
