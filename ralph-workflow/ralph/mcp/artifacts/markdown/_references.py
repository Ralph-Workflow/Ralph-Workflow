"""Stable-ID validation and reference-integrity helpers for markdown artifacts."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from ralph.mcp.artifacts.markdown._document import ParsedItem

_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


def normalize_id(identifier: str, *, case_sensitive: bool = True) -> str | None:
    """Return a grammar-valid ID key, or ``None`` for malformed input."""
    if _ID.fullmatch(identifier) is None:
        return None
    return identifier if case_sensitive else identifier.casefold()


def validate_unique_ids(
    items: Iterable[ParsedItem],
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
) -> list[Diagnostic]:
    """Report references whose target stable ID does not exist.

    ``references`` maps a target ID to ``(source_id, line, section)`` entries;
    the source ID is intentionally retained for diagnostics rather than used as
    a lookup key.
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
                diagnostics.append(
                    Diagnostic(line, section, "REF003", f"{source!r} references unknown ID {target!r}")
                )
    return diagnostics


def validate_acyclic_dependencies(
    dependencies: Mapping[str, Iterable[str]],
    *,
    line_by_id: Mapping[str, int] | None = None,
    section_by_id: Mapping[str, str | None] | None = None,
    case_sensitive: bool = True,
) -> list[Diagnostic]:
    """Report dependency cycles with a stable message naming the re-entered ID."""
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
            diagnostics.append(
                Diagnostic(
                    1 if line_by_id is None else line_by_id.get(identifier, 1),
                    None if section_by_id is None else section_by_id.get(identifier),
                    "REF004",
                    f"dependency cycle detected at ID {identifier!r}",
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
    "normalize_id",
    "validate_acyclic_dependencies",
    "validate_references",
    "validate_unique_ids",
]
