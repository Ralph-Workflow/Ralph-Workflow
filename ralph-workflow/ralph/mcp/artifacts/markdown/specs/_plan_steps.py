"""First-wins discovery of usable canonical step identifiers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
    from ralph.mcp.artifacts.markdown._document import ParsedDocument

PLAN_STEP_ID_PATTERN = re.compile(r"^S-(?P<number>[1-9][0-9]*)$")


def step_number_map(document: ParsedDocument, diagnostics: list[Diagnostic]) -> dict[str, int]:
    """Extract canonical identifiers without diagnosing optional plan shape."""
    numbers: dict[str, int] = {}
    for section in document.sections:
        for block in section.blocks:
            match = PLAN_STEP_ID_PATTERN.fullmatch(block.identifier)
            if match is not None:
                numbers.setdefault(block.identifier, int(match.group("number")))
    return numbers


__all__ = ["PLAN_STEP_ID_PATTERN", "step_number_map"]
