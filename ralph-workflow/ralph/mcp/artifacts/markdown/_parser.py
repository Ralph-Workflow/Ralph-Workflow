"""Pure parser for the deliberately small markdown artifact grammar.

The parser recognizes exactly four content shapes inside ``## Section``
headings: stable-ID list items (``- [ID] text``), indented item
continuation lines (``  Key: value``), stable-ID sub-blocks
(``### [ID] Title`` followed by free body lines), and plain body lines.
It never rejects a shape by itself — which shapes a given section may
use is a per-section :class:`SectionRule` decision enforced by the
shared structure validator, so the parser stays spec-agnostic and a
truncated document still yields a line-anchored parse.
"""

from __future__ import annotations

import re
from typing import cast

from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown._document import ParsedDocument
from ralph.mcp.artifacts.markdown._parsed_block import ParsedBlock
from ralph.mcp.artifacts.markdown._parsed_item import ParsedItem
from ralph.mcp.artifacts.markdown._parsed_line import ParsedLine
from ralph.mcp.artifacts.markdown._parsed_section import ParsedSection

_FRONTMATTER_FIELD = re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9_-]*): (?P<value>\S(?:.*\S)?)$")
_HEADING = re.compile(r"^## (?P<name>\S(?:.*\S)?)$")
_BLOCK_HEADING = re.compile(
    r"^### \[(?P<identifier>[A-Za-z][A-Za-z0-9_-]*)\] (?P<title>\S(?:.*\S)?)$"
)
_ITEM = re.compile(r"^- (?:(?P<checked>\[[ xX]\]) )?\[(?P<identifier>[A-Za-z][A-Za-z0-9_-]*)\] (?P<text>\S(?:.*\S)?)$")
_LIST_PREFIX = re.compile(r"^- (?:\[[ xX]\] )?")


class _SectionBuilder:
    """Mutable accumulator for one section while the parser scans lines."""

    def __init__(self, name: str, line: int) -> None:
        self.name = name
        self.line = line
        self.lines: list[ParsedLine] = []  # bounded-accumulator-ok: bounded by one document's line count within a single parse call
        self.items: list[ParsedItem] = []  # bounded-accumulator-ok: bounded by one document's line count within a single parse call
        self.blocks: list[ParsedBlock] = []  # bounded-accumulator-ok: bounded by one document's line count within a single parse call
        self.block: ParsedBlock | None = None
        self.block_lines: list[ParsedLine] = []  # bounded-accumulator-ok: bounded by one document's line count within a single parse call

    def finish_block(self) -> None:
        if self.block is not None:
            self.blocks.append(
                ParsedBlock(
                    self.block.identifier,
                    self.block.title,
                    self.block.line,
                    tuple(self.block_lines),
                )
            )
        self.block = None
        self.block_lines = []

    def finish(self) -> ParsedSection:
        self.finish_block()
        return ParsedSection(
            self.name, self.line, tuple(self.lines), tuple(self.items), tuple(self.blocks)
        )


def parse_markdown_document(text: str) -> tuple[ParsedDocument, list[Diagnostic]]:
    """Parse ``text`` without raising, returning every recoverable grammar error."""
    diagnostics: list[Diagnostic] = []
    lines = text.splitlines()
    frontmatter: dict[str, str] = {}
    frontmatter_lines: dict[str, int] = {}
    start = 0
    if lines and lines[0] == "---":
        start = _parse_frontmatter(lines, frontmatter, frontmatter_lines, diagnostics)

    sections: list[ParsedSection] = []
    current: _SectionBuilder | None = None
    last_item: ParsedItem | None = None
    item_fields: list[ParsedLine] = []

    def finish_item() -> None:
        nonlocal last_item
        if last_item is not None and current is not None:
            current.items.append(
                ParsedItem(
                    identifier=last_item.identifier,
                    text=last_item.text,
                    line=last_item.line,
                    checked=last_item.checked,
                    fields=tuple(item_fields),
                )
            )
        last_item = None
        item_fields.clear()

    for line_number, line in enumerate(lines[start:], start=start + 1):
        heading = _HEADING.fullmatch(line)
        if heading is not None:
            finish_item()
            if current is not None:
                sections.append(current.finish())
            current = _SectionBuilder(heading.group("name"), line_number)
            continue
        if not line.strip():
            continue
        if current is None:
            diagnostics.append(
                Diagnostic(line_number, None, "MD002", "content must be inside a '## Heading' section")
            )
            continue
        block_heading = _BLOCK_HEADING.fullmatch(line)
        if block_heading is not None:
            finish_item()
            current.finish_block()
            current.block = ParsedBlock(
                block_heading.group("identifier"), block_heading.group("title"), line_number, ()
            )
            continue
        if line.startswith("#"):
            diagnostics.append(
                Diagnostic(
                    line_number,
                    current.name,
                    "MD001",
                    "headings must use '## Section' or '### [ID] Title'",
                )
            )
            continue
        if current.block is not None:
            current.block_lines.append(ParsedLine(line_number, line.strip()))
            continue
        item = _ITEM.fullmatch(line)
        if item is not None:
            finish_item()
            checkbox = cast("str | None", item.group("checked"))
            identifier = cast("str", item.group("identifier"))
            item_text = cast("str", item.group("text"))
            last_item = ParsedItem(
                identifier=identifier,
                text=item_text,
                line=line_number,
                checked=None if checkbox is None else checkbox.lower() == "[x]",
            )
            continue
        if line[0].isspace() and last_item is not None:
            item_fields.append(ParsedLine(line_number, line.strip()))
            continue
        finish_item()
        current.lines.append(ParsedLine(line_number, line.strip()))
    finish_item()
    if current is not None:
        sections.append(current.finish())
    return ParsedDocument(frontmatter, frontmatter_lines, tuple(sections)), diagnostics


def stray_line_diagnostic(line: ParsedLine, section: str | None) -> Diagnostic:
    """Describe a body line found in a section whose rule forbids body content."""
    if _LIST_PREFIX.match(line.text) is not None:
        return Diagnostic(
            line.line,
            section,
            "MD003",
            "list items must use '- [ID] text' or '- [ ] [ID] text'",
        )
    return Diagnostic(line.line, section, "MD004", "section content must be a stable-ID list item")


def _parse_frontmatter(
    lines: list[str],
    frontmatter: dict[str, str],
    frontmatter_lines: dict[str, int],
    diagnostics: list[Diagnostic],
) -> int:
    """Parse a leading frontmatter block and return the next unconsumed index."""
    for index in range(1, len(lines)):
        line = lines[index]
        line_number = index + 1
        if line == "---":
            return index + 1
        field = _FRONTMATTER_FIELD.fullmatch(line)
        if field is None:
            diagnostics.append(
                Diagnostic(line_number, None, "MD005", "frontmatter fields must use 'key: value'")
            )
            continue
        key = field.group("key")
        if key in frontmatter:
            diagnostics.append(Diagnostic(line_number, None, "MD006", f"duplicate frontmatter field {key!r}"))
            continue
        frontmatter[key] = field.group("value")
        frontmatter_lines[key] = line_number
    diagnostics.append(Diagnostic(1, None, "MD007", "unterminated frontmatter block"))
    return len(lines)


parse_document = parse_markdown_document

__all__ = ["parse_document", "parse_markdown_document", "stray_line_diagnostic"]
