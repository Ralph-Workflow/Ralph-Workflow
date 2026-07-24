"""Document-wide step discovery and reference resolution for Markdown plans."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.mcp.artifacts.markdown._document import ParsedDocument
    from ralph.mcp.artifacts.markdown._parsed_line import ParsedLine

PLAN_STEP_ID_PATTERN = re.compile(r"^S-(?P<number>[1-9][0-9]*)$")
_MALFORMED_STEP_LIKE_ID_PATTERN = re.compile(r"^(?:S|STEP)-[0-9]+$", re.IGNORECASE)


def step_number_map(
    document: ParsedDocument, diagnostics: list[Diagnostic]
) -> dict[str, int]:
    """Collect globally unique steps and diagnose numeric step-ID near misses."""
    numbers: dict[str, int] = {}
    for section in document.sections:
        for block in section.blocks:
            match = PLAN_STEP_ID_PATTERN.fullmatch(block.identifier)
            if match is None:
                if _MALFORMED_STEP_LIKE_ID_PATTERN.fullmatch(block.identifier):
                    diagnostics.append(
                        Diagnostic(
                            block.line,
                            section.name,
                            "PLAN022",
                            f"step ID {block.identifier!r} must use the "
                            "S-<positive-number> form",
                        )
                    )
                continue
            if block.identifier in numbers:
                diagnostics.append(
                    Diagnostic(
                        block.line,
                        section.name,
                        "PLAN022",
                        f"duplicate step ID {block.identifier!r}; "
                        "step IDs are document-wide",
                    )
                )
                continue
            numbers[block.identifier] = int(match.group("number"))
    return numbers


def resolve_step_references(
    entries: list[ParsedLine],
    numbers: Mapping[str, int],
    *,
    section: str,
    context: str,
    diagnostics: list[Diagnostic],
) -> list[int]:
    """Resolve stable step IDs to canonical positive numbers."""
    resolved: list[int] = []
    for entry in entries:
        number = numbers.get(entry.text)
        if number is None:
            diagnostics.append(
                Diagnostic(
                    entry.line,
                    section,
                    "PLAN021",
                    f"{context} references unknown step ID {entry.text!r}",
                )
            )
            continue
        resolved.append(number)
    return resolved


__all__ = ["PLAN_STEP_ID_PATTERN", "resolve_step_references", "step_number_map"]
