"""One stable-ID list item parsed from a markdown artifact section."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._parsed_line import ParsedLine


@dataclass(frozen=True)
class ParsedItem:
    """A stable-ID list item from one section.

    ``fields`` carries the indented continuation lines that immediately
    follow the item (``  Key: value``); whether those lines are allowed is
    a per-section rule enforced by the shared structure validator.
    """

    identifier: str
    text: str
    line: int
    checked: bool | None
    fields: tuple[ParsedLine, ...] = ()


__all__ = ["ParsedItem"]
