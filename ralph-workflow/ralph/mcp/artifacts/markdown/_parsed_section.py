
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._parsed_block import ParsedBlock
    from ralph.mcp.artifacts.markdown._parsed_item import ParsedItem
    from ralph.mcp.artifacts.markdown._parsed_line import ParsedLine


@dataclass(frozen=True)
class ParsedSection:

    name: str
    line: int
    level: int
    lines: tuple[ParsedLine, ...]
    items: tuple[ParsedItem, ...]
    blocks: tuple[ParsedBlock, ...]


__all__ = ["ParsedSection"]
