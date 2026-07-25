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
_MALFORMED_STEP_LIKE_ID_PATTERN = re.compile(r"^(?:S|STEP)-[A-Za-z0-9_-]*$", re.IGNORECASE)

# Pipeline consumer phrase for step-ID and step-reference errors.
# Kept in lockstep with ``specs/plan.py`` so every step-shape diagnostic
# names the same downstream reader.
_STEP_PROOF_CONSUMER = (
    "blocking because the development_result 'Plan Items Proven' proof in "
    "ralph/phases/execution.py cross-references step numbers from this plan"
)


def _consumer_rule_message(what: str, fix: str, consumer: str) -> str:
    """Compose a blocking-severity diagnostic that names its downstream consumer."""
    return f"{what}; {consumer}; resolve by {fix}"


def step_number_map(
    document: ParsedDocument, diagnostics: list[Diagnostic]
) -> dict[str, int]:
    """Collect globally unique steps and diagnose step-ID near misses."""
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
                            _consumer_rule_message(
                                f"step ID {block.identifier!r} must use the "
                                "S-<positive-number> form",
                                "rewriting the heading to '### [S-<positive-number>] Title' "
                                "(e.g. '### [S-1] Title')",
                                _STEP_PROOF_CONSUMER,
                            ),
                        )
                    )
                continue
            if block.identifier in numbers:
                diagnostics.append(
                    Diagnostic(
                        block.line,
                        section.name,
                        "PLAN022",
                        _consumer_rule_message(
                            f"duplicate step ID {block.identifier!r}",
                            "renumbering the duplicated step with a new positive "
                            "number and updating every 'Depends on:' / 'Satisfied by:' "
                            "reference to the surviving step",
                            _STEP_PROOF_CONSUMER,
                        ),
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
                    _consumer_rule_message(
                        f"{context} references unknown step ID {entry.text!r}",
                        "renaming the reference to an existing '### [S-n]' heading or "
                        "adding the missing step",
                        _STEP_PROOF_CONSUMER,
                    ),
                )
            )
            continue
        resolved.append(number)
    return resolved


__all__ = ["PLAN_STEP_ID_PATTERN", "resolve_step_references", "step_number_map"]
