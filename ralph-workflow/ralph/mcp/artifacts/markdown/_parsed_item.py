from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._parsed_line import ParsedLine


@dataclass(frozen=True)
class ParsedItem:
    identifier: str
    text: str
    line: int
    checked: bool | None
    fields: tuple[ParsedLine, ...] = ()


__all__ = ["ParsedItem"]
