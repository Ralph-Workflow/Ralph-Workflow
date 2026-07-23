"""One named section in a parsed markdown artifact."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._parsed_item import ParsedItem


@dataclass(frozen=True)
class ParsedSection:
    """A named document section and its closed-grammar items."""

    name: str
    line: int
    raw_lines: tuple[str, ...]
    items: tuple[ParsedItem, ...]


__all__ = ["ParsedSection"]
