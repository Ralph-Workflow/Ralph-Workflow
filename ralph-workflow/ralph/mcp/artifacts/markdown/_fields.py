"""Labeled ``Field: value`` line parsing shared by markdown artifact specs.

The closed grammar recognizes three field kinds against a per-context key
table:

- ``scalar`` — ``Field: value`` single-value line.
- ``inline_list`` — ``Field: a, b`` comma-separated entries (IDs, names,
  enum words; never free prose that may contain commas).
- ``bullet_list`` — ``Field:`` opening line followed by ``- entry``
  bullets (prose, paths, commands).

Lines that match no known label are prose where the context allows it
(with a warning when they look like a mistyped label) and a hard,
line-anchored error where it does not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, cast

from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown._parsed_line import ParsedLine

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

_FIELD_LABEL = re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9 _-]{0,30}):(?: (?P<value>\S(?:.*\S)?))?$")

type FieldKind = Literal["scalar", "inline_list", "bullet_list"]


@dataclass
class ParsedFields:
    """Fields recognized in one body: scalar values, list entries, prose."""

    scalars: dict[str, ParsedLine] = field(default_factory=dict)
    lists: dict[str, list[ParsedLine]] = field(default_factory=dict)
    prose: list[ParsedLine] = field(default_factory=list)


def parse_fields(
    lines: Iterable[ParsedLine],
    table: Mapping[str, FieldKind],
    *,
    section: str,
    context: str,
    prose_allowed: bool,
    diagnostics: list[Diagnostic],
) -> ParsedFields:
    """Interpret body lines against one closed field table."""
    result = ParsedFields()
    open_list: str | None = None
    for line in lines:
        if line.text.startswith("- "):
            _consume_bullet(
                result, line, open_list, section, context, prose_allowed, diagnostics
            )
            continue
        open_list = _consume_field_line(
            result, line, table, section, context, prose_allowed, diagnostics
        )
    return result


def _consume_bullet(
    result: ParsedFields,
    line: ParsedLine,
    open_list: str | None,
    section: str,
    context: str,
    prose_allowed: bool,
    diagnostics: list[Diagnostic],
) -> None:
    entry = line.text[2:].strip()
    if open_list is not None:
        if entry:
            result.lists[open_list].append(ParsedLine(line.line, entry))
    elif prose_allowed:
        result.prose.append(line)
    else:
        diagnostics.append(
            Diagnostic(
                line.line,
                section,
                "PLAN020",
                f"{context}: bullet lines must follow a list field such as 'Files:'",
            )
        )


def _consume_field_line(
    result: ParsedFields,
    line: ParsedLine,
    table: Mapping[str, FieldKind],
    section: str,
    context: str,
    prose_allowed: bool,
    diagnostics: list[Diagnostic],
) -> str | None:
    """Consume one non-bullet line; return the list key it opens, if any."""
    label = _FIELD_LABEL.fullmatch(line.text)
    key = cast("str | None", label.group("key")) if label is not None else None
    kind = table.get(key.strip().casefold()) if key is not None else None
    if key is None or kind is None:
        _consume_unlabeled(result, line, key, section, context, prose_allowed, diagnostics)
        return None
    canonical = key.strip().casefold()
    if canonical in result.scalars or canonical in result.lists:
        diagnostics.append(
            Diagnostic(line.line, section, "PLAN020", f"{context}: duplicate field {key!r}")
        )
        return None
    value = cast("str | None", label.group("value")) if label is not None else None
    if kind == "bullet_list":
        if value is not None:
            diagnostics.append(
                Diagnostic(
                    line.line,
                    section,
                    "PLAN020",
                    f"{context}: field {key!r} takes '- ' bullet lines, not an inline value",
                )
            )
            return None
        result.lists[canonical] = []
        return canonical
    if value is None:
        diagnostics.append(
            Diagnostic(line.line, section, "PLAN020", f"{context}: field {key!r} requires a value")
        )
        return None
    if kind == "scalar":
        result.scalars[canonical] = ParsedLine(line.line, value)
    else:
        result.lists[canonical] = [
            ParsedLine(line.line, part.strip()) for part in value.split(",") if part.strip()
        ]
    return None


def _consume_unlabeled(
    result: ParsedFields,
    line: ParsedLine,
    key: str | None,
    section: str,
    context: str,
    prose_allowed: bool,
    diagnostics: list[Diagnostic],
) -> None:
    if prose_allowed:
        result.prose.append(line)
        if key is not None:
            diagnostics.append(
                Diagnostic(
                    line.line,
                    section,
                    "PLAN009",
                    f"{context}: unrecognized field label {key!r} treated as prose",
                    "warning",
                )
            )
    else:
        diagnostics.append(
            Diagnostic(
                line.line,
                section,
                "PLAN020",
                f"{context}: lines must use one of the documented 'Field: value' labels",
            )
        )


__all__ = ["FieldKind", "ParsedFields", "parse_fields"]
