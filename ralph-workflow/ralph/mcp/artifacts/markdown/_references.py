"""Stable-ID validation and reference-integrity helpers for markdown artifacts."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Protocol

from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


class StableIdNode(Protocol):
    """Any parsed node that carries a stable ID and a source line."""

    @property
    def identifier(self) -> str: ...

    @property
    def line(self) -> int: ...


def normalize_id(identifier: str, *, case_sensitive: bool = True) -> str | None:
    """Return a grammar-valid ID key, or ``None`` for malformed input."""
    if _ID.fullmatch(identifier) is None:
        return None
    return identifier if case_sensitive else identifier.casefold()


# Consumer phrases used by reference validators. Each validator picks the
# consumer that matches the section it is validating so the diagnostic
# names the reader that actually breaks on the bad reference.
_STEP_PROOF_CONSUMER = (
    "blocking because the development_result 'Plan Items Proven' proof in "
    "ralph/phases/execution.py cross-references step numbers from this plan"
)
_FAN_OUT_CONSUMER = (
    "blocking because the worker fan-out in ralph/pipeline/work_units.py "
    "parses ## Work Units / ## Parallel Plan IDs to scope edits and dispatch units"
)


def _consumer_rule_message(what: str, fix: str, consumer: str) -> str:
    """Compose a blocking-severity diagnostic that names its downstream consumer."""
    return f"{what}; {consumer}; resolve by {fix}"


def _consumer_for_section(section: str | None) -> str:
    """Return the named consumer phrase for the section a reference is read in."""
    if section in {"Work Units", "Parallel Plan"}:
        return _FAN_OUT_CONSUMER
    # Default: development_result proof reads steps / acceptance-criteria
    # / design references. The reference validator passes through ``section``
    # from the call site, so any plan-side caller that wants a different
    # consumer should pass an explicit ``consumer`` to ``validate_references``.
    return _STEP_PROOF_CONSUMER


def validate_unique_ids(
    items: Iterable[StableIdNode],
    *,
    section: str | None = None,
    case_sensitive: bool = True,
) -> list[Diagnostic]:
    """Report malformed or duplicate stable IDs without throwing."""
    diagnostics: list[Diagnostic] = []
    seen: set[str] = set()
    for item in items:
        key = normalize_id(item.identifier, case_sensitive=case_sensitive)
        if key is None:
            diagnostics.append(Diagnostic(item.line, section, "REF001", f"malformed ID {item.identifier!r}"))
        elif key in seen:
            diagnostics.append(Diagnostic(item.line, section, "REF002", f"duplicate ID {item.identifier!r}"))
        else:
            seen.add(key)
    return diagnostics


def validate_references(
    references: Mapping[str, Iterable[tuple[str, int, str | None]]],
    known_ids: Iterable[str],
    *,
    case_sensitive: bool = True,
    consumer: str | None = None,
) -> list[Diagnostic]:
    """Report references whose target stable ID does not exist.

    ``references`` maps a target ID to ``(source_id, line, section)`` entries;
    the source ID is intentionally retained for diagnostics rather than used as
    a lookup key. ``consumer`` overrides the consumer phrase selected from
    the ``section`` argument; pass ``consumer`` for plan step references whose
    consumer is the development_result proof and whose ``section`` may be
    ``"Steps"`` (the default ``_consumer_for_section`` already picks that).
    """
    known = {
        key
        for identifier in known_ids
        if (key := normalize_id(identifier, case_sensitive=case_sensitive)) is not None
    }
    diagnostics: list[Diagnostic] = []
    for target, sources in references.items():
        key = normalize_id(target, case_sensitive=case_sensitive)
        for source, line, section in sources:
            if key is None or key not in known:
                chosen_consumer = consumer if consumer is not None else _consumer_for_section(section)
                diagnostics.append(
                    Diagnostic(
                        line,
                        section,
                        "REF003",
                        _consumer_rule_message(
                            f"{source!r} references unknown ID {target!r}",
                            "renaming the reference to an existing ID or adding the missing entry",
                            chosen_consumer,
                        ),
                    )
                )
    return diagnostics


def validate_acyclic_dependencies(
    dependencies: Mapping[str, Iterable[str]],
    *,
    line_by_id: Mapping[str, int] | None = None,
    section_by_id: Mapping[str, str | None] | None = None,
    case_sensitive: bool = True,
    consumer: str | None = None,
) -> list[Diagnostic]:
    """Report dependency cycles with a stable message naming the re-entered ID.

    ``consumer`` overrides the consumer phrase selected from
    ``section_by_id`` (typically "Work Units" or "Parallel Plan" for plan
    fan-out dependency cycles, otherwise the step-proof consumer).
    """
    graph = {
        _required_id(identifier, case_sensitive): tuple(
            _required_id(dependency, case_sensitive) for dependency in values
        )
        for identifier, values in dependencies.items()
    }
    visiting: set[str] = set()
    visited: set[str] = set()
    diagnostics: list[Diagnostic] = []

    def visit(identifier: str) -> None:
        if identifier in visited or diagnostics:
            return
        if identifier in visiting:
            section = None if section_by_id is None else section_by_id.get(identifier)
            chosen_consumer = (
                consumer if consumer is not None else _consumer_for_section(section)
            )
            diagnostics.append(
                Diagnostic(
                    1 if line_by_id is None else line_by_id.get(identifier, 1),
                    section,
                    "REF004",
                    _consumer_rule_message(
                        f"dependency cycle detected at ID {identifier!r}",
                        "removing one of the cycle edges (e.g. drop a 'Depends on:' link)",
                        chosen_consumer,
                    ),
                )
            )
            return
        visiting.add(identifier)
        for dependency in graph.get(identifier, ()):
            visit(dependency)
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in graph:
        visit(identifier)
    return diagnostics


def _required_id(identifier: str, case_sensitive: bool) -> str:
    key = normalize_id(identifier, case_sensitive=case_sensitive)
    if key is None:
        return identifier
    return key


__all__ = [
    "StableIdNode",
    "normalize_id",
    "validate_acyclic_dependencies",
    "validate_references",
    "validate_unique_ids",
]
