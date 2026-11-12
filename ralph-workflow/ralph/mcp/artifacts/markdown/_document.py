from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._parsed_section import ParsedSection


@dataclass(frozen=True)
class ParsedDocument:
    frontmatter: dict[str, str]
    frontmatter_lines: dict[str, int]
    sections: tuple[ParsedSection, ...]

    def section(self, name: str) -> ParsedSection | None:
        return next((section for section in self.sections if section.name == name), None)

    def sections_named(self, name: str) -> tuple[ParsedSection, ...]:
        return tuple(section for section in self.sections if section.name == name)


__all__ = ["ParsedDocument"]
