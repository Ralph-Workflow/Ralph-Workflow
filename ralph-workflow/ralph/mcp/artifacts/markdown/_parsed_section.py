"""One named section parsed from a markdown artifact document."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._parsed_block import ParsedBlock
    from ralph.mcp.artifacts.markdown._parsed_item import ParsedItem
    from ralph.mcp.artifacts.markdown._parsed_line import ParsedLine


@dataclass(frozen=True)
class ParsedSection:
    """A named document section and its closed-grammar content.

    ``lines`` holds section-level body lines (prose or ``Key: value``
    fields), ``items`` the stable-ID list items, and ``blocks`` the
    ``### [ID] Title`` sub-blocks. Which of the three a section may use
    is a per-section rule enforced by the shared structure validator.
    """

    name: str
    line: int
    lines: tuple[ParsedLine, ...]
    items: tuple[ParsedItem, ...]
    blocks: tuple[ParsedBlock, ...]


__all__ = ["ParsedSection"]
