"""First-wins discovery of usable canonical step identifiers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
    from ralph.mcp.artifacts.markdown._document import ParsedDocument

PLAN_STEP_ID_PATTERN = re.compile(r"^S-(?P<number>[1-9][0-9]*)$")
# Python's int() rejects integer strings longer than 4300 digits by default;
# tolerate that boundary during best-effort extraction rather than raising.
_MAX_INT_DIGITS = 4300


def step_number_map(document: ParsedDocument, diagnostics: list[Diagnostic]) -> dict[str, int]:
    """Extract canonical identifiers without diagnosing optional plan shape."""
    numbers: dict[str, int] = {}
    for section in document.sections:
        for block in section.blocks:
            match = PLAN_STEP_ID_PATTERN.fullmatch(block.identifier)
            if match is None:
                continue
            raw = match.group("number")
            if len(raw) > _MAX_INT_DIGITS:
                # Skip oversized numeric tokens rather than aborting extraction.
                continue
            try:
                numbers.setdefault(block.identifier, int(raw))
            except ValueError:
                continue
    return numbers


__all__ = ["PLAN_STEP_ID_PATTERN", "step_number_map"]
