"""Pure parser for the deliberately small markdown artifact grammar."""

from __future__ import annotations

import re
from typing import cast

from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown._document import ParsedDocument, ParsedItem, ParsedSection

_FRONTMATTER_FIELD = re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9_-]*): (?P<value>\S(?:.*\S)?)$")
_HEADING = re.compile(r"^## (?P<name>[A-Za-z][A-Za-z0-9 _-]*)$")
_ITEM = re.compile(r"^- (?:(?P<checked>\[[ xX]\]) )?\[(?P<identifier>[A-Za-z][A-Za-z0-9_-]*)\] (?P<text>\S(?:.*\S)?)$")
_LIST_PREFIX = re.compile(r"^- (?:\[[ xX]\] )?")


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
    current_name: str | None = None
    current_line = 0
    current_raw_lines: list[str] = []
    current_items: list[ParsedItem] = []

    def finish_section() -> None:
        if current_name is not None:
            sections.append(
                ParsedSection(current_name, current_line, tuple(current_raw_lines), tuple(current_items))
            )

    for line_number, line in enumerate(lines[start:], start=start + 1):
        heading = _HEADING.fullmatch(line)
        if heading is not None:
            finish_section()
            current_name = heading.group("name")
            current_line = line_number
            current_raw_lines = []
            current_items = []
            continue
        if line.startswith("#"):
            diagnostics.append(
                Diagnostic(line_number, current_name, "MD001", "headings must use exactly '## Heading'")
            )
            continue
        if not line:
            continue
        if current_name is None:
            diagnostics.append(
                Diagnostic(line_number, None, "MD002", "content must be inside a '## Heading' section")
            )
            continue
        current_raw_lines.append(line)
        item = _ITEM.fullmatch(line)
        if item is not None:
            checkbox = cast("str | None", item.group("checked"))
            identifier = cast("str | None", item.group("identifier"))
            item_text = cast("str | None", item.group("text"))
            if identifier is None or item_text is None:
                diagnostics.append(Diagnostic(line_number, current_name, "MD008", "malformed list item"))
                continue
            current_items.append(
                ParsedItem(
                    identifier=identifier,
                    text=item_text,
                    line=line_number,
                    checked=None if checkbox is None else checkbox.lower() == "[x]",
                )
            )
        elif _LIST_PREFIX.match(line) is not None:
            diagnostics.append(
                Diagnostic(
                    line_number,
                    current_name,
                    "MD003",
                    "list items must use '- [ID] text' or '- [ ] [ID] text'",
                )
            )
        else:
            diagnostics.append(
                Diagnostic(
                    line_number,
                    current_name,
                    "MD004",
                    "section content must be a stable-ID list item",
                )
            )
    finish_section()
    return ParsedDocument(frontmatter, frontmatter_lines, tuple(sections)), diagnostics


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

__all__ = ["parse_document", "parse_markdown_document"]
