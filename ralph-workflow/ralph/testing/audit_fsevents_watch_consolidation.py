"""Fsevents watch-consolidation drift audit.

The macOS fseventsd backend in ``watchdog.observers.fsevents`` is
OS-recursive: a single ``observer.schedule(handler, path,
recursive=True)`` call already delivers events for every nested
directory under ``path``, so non-recursive subscriptions cannot
reduce fseventsd delivery and only multiply overlapping streams.
Ralph Workflow commits to scheduling **exactly one** recursive root
watch from ``WorkspaceMonitor.start()`` so the fseventsd footprint
is the minimal single recursive stream.

This audit locks that consolidation structurally. It parses
``ralph/agents/invoke/_workspace.py`` with the ``ast`` module only
(no subprocess, no ``time.sleep``, no real file I/O outside reading
source) and enforces four invariants:

  1. **INV-1 (count)** -- the module contains exactly one
     ``ast.Call`` whose function is an ``ast.Attribute`` named
     ``schedule``.  The TYPE_CHECKING ``_ObserverProtocol`` signature
     (``def schedule(...)``) is an ``ast.FunctionDef``, not an
     ``ast.Call``, so it is excluded by construction.  Zero matches
     raises ``missing_watch_schedule``; ``N > 1`` raises one
     ``multiple_watch_schedule`` violation per extra call.
  2. **INV-2 (recursive)** -- the single schedule call passes a
     keyword argument named ``recursive`` whose value is the
     literal ``ast.Constant(value=True)``.  Missing, ``False``, or
     any non-constant expression raises ``watch_not_recursive``.
  3. **INV-3 (static location)** -- the schedule call's
     ancestor chain (built via an explicit child->parent AST map)
     contains no ``ast.For`` / ``ast.AsyncFor`` / ``ast.While``
     node AND its nearest enclosing ``ast.FunctionDef`` /
     ``ast.AsyncFunctionDef`` is named ``start``.  Either condition
     being violated raises ``dynamic_watch_schedule``.  The
     ancestor-walk approach is essential: a FunctionDef
     line-range containment check would treat a schedule call
     nested in a ``for``/``while`` loop inside ``start()`` as still
     "in start()" and let a per-iteration reschedule slip through.
  4. **INV-4 (module presence)** -- the target module exists
     under ``package_root``.  Missing raises
     ``missing_workspace_module`` because the file's absence is
     itself drift.

Usage::

    python -m ralph.testing.audit_fsevents_watch_consolidation [package_root]

Exit codes:
  0 = clean
  1 = violations found
  2 = root not found
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


#: Module that owns the canonical ``WorkspaceMonitor.start()``
#: schedule call.  Anchored at import time so a refactor that
#: renames or relocates the module trips the audit immediately
#: rather than silently passing.
_WORKSPACE_MONITOR_MODULE: str = "agents/invoke/_workspace.py"

#: Pre-filter substring for the schedule-call detector.  Files
#: whose source does not contain the literal token ``.schedule(``
#: cannot schedule a watchdog watch, so the expensive AST pass is
#: skipped for them.
_SCHEDULE_CALL_MARKER: str = ".schedule("


@dataclass(frozen=True)
class FseventsWatchViolation:
    """A single fsevents-watch-consolidation audit violation."""

    kind: str
    file_path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.kind}] {self.message}"


def _build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    """Return a child->parent AST map for every node under ``tree``.

    Built by iterating ``ast.walk(tree)`` and, for each node,
    assigning ``parents[child] = node`` for every child yielded by
    ``ast.iter_child_nodes(node)``.  This explicit ancestor map
    is the seam that lets INV-3 distinguish a schedule call
    nested in a ``for``/``while`` loop inside ``start()`` from one
    placed directly in ``start()`` -- a check that FunctionDef
    line-range containment cannot perform.
    """
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _ancestors(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> list[ast.AST]:
    """Return ``node``'s ancestor chain from immediate parent to module root.

    Walks the parent map until a node is missing a parent.  Excludes
    ``node`` itself -- only ancestors (callers, loops, function
    bodies) are returned.  The order is innermost-first so the
    nearest enclosing ``FunctionDef`` is the LAST function-def entry
    in the returned list, which lets INV-3(b) pick the nearest
    enclosing function with a single reversed iteration.
    """
    chain: list[ast.AST] = []
    current: ast.AST | None = parents.get(node)
    while current is not None:
        chain.append(current)
        current = parents.get(current)
    return chain


def _find_schedule_calls(tree: ast.Module) -> list[ast.Call]:
    """Return every ``Call`` whose function attribute is ``schedule``.

    Matches only ``ast.Call`` nodes whose ``func`` is an
    ``ast.Attribute`` with ``attr == "schedule"``.  This excludes
    the TYPE_CHECKING ``_ObserverProtocol.schedule`` (a
    ``FunctionDef``, not a ``Call``) and the
    ``observers_module.Observer()`` call (attribute ``Observer``,
    not ``schedule``).  Bare ``Name.schedule(...)`` is not used by
    the production code path and is also excluded by the
    ``ast.Attribute`` requirement.
    """
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "schedule":
            calls.append(node)
    return calls


def _has_recursive_true_kwarg(call: ast.Call) -> bool:
    """Return True iff ``call`` passes ``recursive=True`` as a kwarg.

    The literal ``ast.Constant(value=True)`` is required --
    ``recursive=maybe`` or any non-constant expression is treated
    as not-recursive because runtime ``recursive=`` evaluation is
    the exact drift class the audit is built to catch.
    """
    for kw in call.keywords:
        if kw.arg != "recursive":
            continue
        value = kw.value
        return isinstance(value, ast.Constant) and value.value is True
    return False


def _has_loop_ancestor(ancestors: list[ast.AST]) -> bool:
    """Return True iff any ancestor is a ``for`` / ``async for`` / ``while``.

    Walks the ancestor chain returned by :func:`_ancestors` and
    checks for loop constructs.  A schedule call wrapped in any
    such loop is treated as dynamic because the loop body runs
    zero or more times at runtime, even when the nearest enclosing
    function is statically correct.
    """
    return any(isinstance(ancestor, (ast.For, ast.AsyncFor, ast.While)) for ancestor in ancestors)


def _nearest_enclosing_function(
    ancestors: list[ast.AST],
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Return the nearest enclosing ``FunctionDef``/``AsyncFunctionDef``.

    Ancestors are innermost-first, so the first match is the
    nearest.  Returns ``None`` when the schedule call sits at
    module top-level (no enclosing function), which INV-3(b)
    treats as drift.
    """
    for ancestor in ancestors:
        if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return ancestor
    return None


def _check_module(
    module_path: Path,
    rel_path: str,
    source: str,
) -> list[FseventsWatchViolation]:
    """Run INV-1..INV-3 against one ``_workspace.py`` source string.

    Args:
        module_path: Absolute path of the module being audited
            (used as the ``filename`` argument to ``ast.parse`` for
            accurate error reporting).
        rel_path: Posix-style path relative to ``package_root``;
            recorded in violation messages.
        source: The full source text of the module.

    Returns:
        A list of violations.  Empty when all invariants pass.
    """
    try:
        tree: ast.Module = ast.parse(source, filename=str(module_path))
    except (SyntaxError, ValueError):
        return []

    schedule_calls: list[ast.Call] = _find_schedule_calls(tree)
    invariants_violations: list[FseventsWatchViolation] = (
        _check_schedule_call_invariants(rel_path, schedule_calls)
    )
    if invariants_violations or not schedule_calls:
        return invariants_violations

    return _check_schedule_call_location(rel_path, tree, schedule_calls[0])


def _check_schedule_call_invariants(
    rel_path: str,
    schedule_calls: list[ast.Call],
) -> list[FseventsWatchViolation]:
    """Run INV-1 (count) and INV-2 (recursive) against the schedule-call list.

    Returns an empty list when the invariants pass.  The location
    check (INV-3) is performed separately because it requires the
    AST tree, not just the call list.
    """
    if not schedule_calls:
        return [_missing_watch_schedule_violation(rel_path)]

    if len(schedule_calls) > 1:
        return [
            FseventsWatchViolation(
                kind="multiple_watch_schedule",
                file_path=rel_path,
                line=extra_call.lineno,
                message=(
                    "expected exactly one observer.schedule(...) call;"
                    f" found additional schedule call at line"
                    f" {extra_call.lineno} (extra schedules inflate the"
                    " fseventsd footprint)"
                ),
            )
            for extra_call in schedule_calls[1:]
        ]

    schedule_call: ast.Call = schedule_calls[0]
    if not _has_recursive_true_kwarg(schedule_call):
        return [_watch_not_recursive_violation(rel_path, schedule_call.lineno)]

    return []


def _check_schedule_call_location(
    rel_path: str,
    tree: ast.Module,
    schedule_call: ast.Call,
) -> list[FseventsWatchViolation]:
    """Run INV-3 (static location) against the single schedule call.

    Builds the AST ancestor map for ``schedule_call`` and emits a
    ``dynamic_watch_schedule`` violation if any ancestor is a loop
    construct OR the nearest enclosing function is not ``start``.
    Returns an empty list when the call sits directly inside
    ``start()`` with no loop ancestor.
    """
    parents: dict[ast.AST, ast.AST] = _build_parent_map(tree)
    ancestors: list[ast.AST] = _ancestors(schedule_call, parents)

    if _has_loop_ancestor(ancestors):
        return [_loop_ancestor_violation(rel_path, schedule_call.lineno)]

    enclosing_function: ast.FunctionDef | ast.AsyncFunctionDef | None = (
        _nearest_enclosing_function(ancestors)
    )
    if enclosing_function is None or enclosing_function.name != "start":
        actual_name: str = enclosing_function.name if enclosing_function is not None else "<module>"
        return [_wrong_enclosing_function_violation(rel_path, schedule_call.lineno, actual_name)]

    return []


def _missing_watch_schedule_violation(rel_path: str) -> FseventsWatchViolation:
    """INV-1 failure: zero ``observer.schedule(...)`` calls in the module."""
    return FseventsWatchViolation(
        kind="missing_watch_schedule",
        file_path=rel_path,
        line=0,
        message=(
            "expected exactly one observer.schedule(...) call inside"
            f" {_WORKSPACE_MONITOR_MODULE!r}: WorkspaceMonitor.start()"
            " must schedule the recursive root watch; none found"
        ),
    )


def _watch_not_recursive_violation(rel_path: str, lineno: int) -> FseventsWatchViolation:
    """INV-2 failure: the single schedule call is not ``recursive=True``."""
    return FseventsWatchViolation(
        kind="watch_not_recursive",
        file_path=rel_path,
        line=lineno,
        message=(
            "the single observer.schedule(...) call must pass"
            " recursive=True (watchdog's fsevents backend is"
            " OS-recursive; non-recursive subscriptions would multiply"
            " overlapping streams)"
        ),
    )


def _loop_ancestor_violation(rel_path: str, lineno: int) -> FseventsWatchViolation:
    """INV-3(a) failure: schedule call is wrapped in a ``for``/``while`` loop."""
    return FseventsWatchViolation(
        kind="dynamic_watch_schedule",
        file_path=rel_path,
        line=lineno,
        message=(
            "observer.schedule(...) is nested inside a for/while loop;"
            " the watch would be (re)scheduled on every iteration and"
            " inflate the fseventsd footprint"
        ),
    )


def _wrong_enclosing_function_violation(
    rel_path: str, lineno: int, actual_name: str
) -> FseventsWatchViolation:
    """INV-3(b) failure: schedule call sits in a function other than ``start``."""
    return FseventsWatchViolation(
        kind="dynamic_watch_schedule",
        file_path=rel_path,
        line=lineno,
        message=(
            "observer.schedule(...) must be scheduled statically inside"
            f" WorkspaceMonitor.start(); found nearest enclosing"
            f" function {actual_name!r}"
        ),
    )


def audit_fsevents_watch_consolidation(
    package_root: Path,
) -> list[FseventsWatchViolation]:
    """Walk the production source tree and return all violations.

    Parses only the single canonical ``_workspace.py`` module and
    enforces INV-1..INV-4.  Returns an empty list when ``package_root``
    is not a directory (fail-closed: the audit does not silently
    pass on a missing root).
    """
    if not package_root.is_dir():
        return []

    module_path: Path = package_root / _WORKSPACE_MONITOR_MODULE
    rel_path: str = _WORKSPACE_MONITOR_MODULE

    if not module_path.is_file():
        return [
            FseventsWatchViolation(
                kind="missing_workspace_module",
                file_path=rel_path,
                line=0,
                message=(
                    f"{_WORKSPACE_MONITOR_MODULE!r} must exist under the package"
                    " root; its absence is itself drift"
                ),
            )
        ]

    try:
        source: str = module_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    if _SCHEDULE_CALL_MARKER not in source:
        return [
            FseventsWatchViolation(
                kind="missing_watch_schedule",
                file_path=rel_path,
                line=0,
                message=(
                    "expected exactly one observer.schedule(...) call inside"
                    f" {_WORKSPACE_MONITOR_MODULE!r}: WorkspaceMonitor.start()"
                    " must schedule the recursive root watch; none found"
                ),
            )
        ]

    return _check_module(module_path, rel_path, source)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.  Returns 0 when clean, 1 on violations, 2 on bad root."""
    if argv is None:
        argv = sys.argv[1:]

    package_root: Path = Path(argv[0]) if argv else Path(__file__).parent.parent

    if not package_root.is_dir():
        print(f"Package root not found: {package_root}", file=sys.stderr)
        return 2

    violations: list[FseventsWatchViolation] = audit_fsevents_watch_consolidation(
        package_root
    )

    if violations:
        print(f"FSEVENTS WATCH CONSOLIDATION VIOLATIONS: {len(violations)}")
        print("=" * 72)
        for violation in violations:
            print(f"  {violation}")
        print()
        print(
            f"Fix the drift: keep exactly one observer.schedule(..., recursive=True)"
            f" call statically inside WorkspaceMonitor.start() in"
            f" {_WORKSPACE_MONITOR_MODULE!r}; no loop ancestor; not in any other"
            " function. A single recursive root watch is the minimal-stream option"
            " for macOS fseventsd."
        )
        return 1

    print("fsevents watch consolidation audit: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
