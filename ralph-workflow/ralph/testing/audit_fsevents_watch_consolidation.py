"""Fsevents watch-consolidation drift audit.

The macOS fseventsd backend in ``watchdog.observers.fsevents`` is
OS-recursive: a single ``observer.schedule(handler, path,
recursive=True)`` call already delivers events for every nested
directory under ``path``, so non-recursive subscriptions cannot
reduce fseventsd delivery and only multiply overlapping streams.
Ralph Workflow commits to scheduling **exactly one** recursive root
watch from ``WorkspaceMonitor.start()`` so the fseventsd footprint
is the minimal single recursive stream.

This audit locks that consolidation structurally. It walks every production
module under ``ralph/`` and permits ``observer.schedule(...)`` only in the
canonical ``WorkspaceMonitor.start()`` owner. It then parses that owner with
the ``ast`` module only (no subprocess, no ``time.sleep``, no real file I/O
outside reading source) and enforces five invariants:

  1. **INV-1 (count)** -- the module contains exactly one direct
     ``ast.Call`` whose function is an ``ast.Attribute`` named
     ``schedule``.  Binding ``.schedule`` to another name is rejected
     as ``aliased_watch_schedule`` so an indirect call cannot evade
     the single-watch check. The TYPE_CHECKING ``_ObserverProtocol`` signature
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
  5. **INV-5 (package-wide ownership)** -- every production module other
     than the canonical owner is scanned for direct ``.schedule(...)`` calls
     and dynamic ``getattr(..., "schedule")`` calls or aliases. Any such use
     raises ``unowned_watch_schedule``. New production modules therefore fail
     closed rather than silently adding an overlapping watch.
  6. **INV-6 (canonical receiver)** -- the canonical call must be the direct
     ``self._observer.schedule(...)`` expression. An unrelated scheduler must
     not satisfy the owner contract while the real observer is hidden behind a
     dynamic lookup.
  7. **INV-7 (observer ownership)** -- every construction of watchdog's
     ``Observer`` outside the canonical workspace-monitor module is rejected.
     Scheduling-only enforcement can otherwise be evaded by creating another
     observer behind a helper and scheduling it later. The lifecycle-owned
     monitor is the sole place that may create the workspace event source.

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

_EXCLUDED_DIRECTORY_NAMES: frozenset[str] = frozenset({"__pycache__"})
_GETATTR_ATTRIBUTE_POSITION: int = 1
_MIN_GETATTR_ARGUMENTS: int = _GETATTR_ATTRIBUTE_POSITION + 1


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


def _is_dynamic_schedule_lookup(node: ast.AST) -> bool:
    """Return whether ``node`` is the literal ``getattr(..., "schedule")`` form."""
    match node:
        case ast.Call(
            func=ast.Name(id="getattr"),
            args=[_, ast.Constant(value="schedule"), *_],
        ):
            return True
        case _:
            return False


def _is_observer_receiver(node: ast.expr) -> bool:
    """Return whether a receiver's name identifies watchdog observer ownership."""
    if isinstance(node, ast.Name):
        return node.id.endswith("observer")
    return isinstance(node, ast.Attribute) and node.attr.endswith("observer")


def _nonliteral_observer_getattr_invocation_line(node: ast.AST) -> int | None:
    """Return an invoked nonliteral lookup on an observer-like receiver.

    Dynamic dispatch unrelated to an observer is not a filesystem-watch
    operation. A nonliteral method invoked on an observer can be ``schedule``,
    however, and must fail closed so it cannot introduce an unowned watch.
    """
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Call):
        return None
    lookup = node.func
    if not isinstance(lookup.func, ast.Name) or lookup.func.id != "getattr":
        return None
    if len(lookup.args) < _MIN_GETATTR_ARGUMENTS:
        return None
    receiver = lookup.args[0]
    attribute = lookup.args[_GETATTR_ATTRIBUTE_POSITION]
    if not _is_observer_receiver(receiver):
        return None
    if isinstance(attribute, ast.Constant) and isinstance(attribute.value, str):
        return None
    return node.lineno


def _observer_constructor_aliases(tree: ast.Module) -> frozenset[str]:
    """Return local names that construct watchdog observers in one module.

    The audit recognizes direct ``from watchdog.observers import Observer``
    imports, including aliases. A new observer is a new watch source even when
    scheduling is deferred through another helper, so ownership must be
    rejected at construction rather than only at ``schedule``.
    """
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != "watchdog.observers":
            continue
        aliases.update(imported.asname or imported.name for imported in node.names if imported.name == "Observer")
    return frozenset(aliases)


def _observer_constructor_lines(tree: ast.Module) -> list[int]:
    """Return lines that construct a directly imported watchdog ``Observer``."""
    aliases = _observer_constructor_aliases(tree)
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in aliases
    ]


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
        if (isinstance(func, ast.Attribute) and func.attr == "schedule") or _is_dynamic_schedule_lookup(
            func
        ):
            calls.append(node)
    return calls


def _find_schedule_aliases(tree: ast.Module) -> list[ast.expr]:
    """Return every ``.schedule`` attribute bound for later invocation.

    The canonical owner must call ``self._observer.schedule(...)`` directly.
    A bound method assignment such as ``schedule = self._observer.schedule``
    can otherwise hide a subsequent invocation from the AST call matcher and
    evade the package-wide single-watch rule.
    """
    aliases: list[ast.expr] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.NamedExpr, ast.AnnAssign)):
            continue
        value = node.value
        if (isinstance(value, ast.Attribute) and value.attr == "schedule") or (
            isinstance(value, ast.Call) and _is_dynamic_schedule_lookup(value)
        ):
            aliases.append(value)
    return aliases


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
    except (SyntaxError, ValueError) as exc:
        return [
            FseventsWatchViolation(
                kind="invalid_workspace_module",
                file_path=rel_path,
                line=exc.lineno if isinstance(exc, SyntaxError) and exc.lineno else 0,
                message=(
                    "canonical workspace monitor source could not be parsed; restore valid "
                    "source so the package-wide watch ownership audit can fail closed"
                ),
            )
        ]

    schedule_aliases: list[ast.expr] = _find_schedule_aliases(tree)
    if schedule_aliases:
        return [
            _aliased_watch_schedule_violation(rel_path, alias.lineno) for alias in schedule_aliases
        ]

    schedule_calls: list[ast.Call] = _find_schedule_calls(tree)
    invariants_violations: list[FseventsWatchViolation] = _check_schedule_call_invariants(
        rel_path, schedule_calls
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


def _is_canonical_schedule_receiver(call: ast.Call) -> bool:
    """Return whether ``call`` directly invokes ``self._observer.schedule``."""
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr != "schedule":
        return False
    receiver = func.value
    return (
        isinstance(receiver, ast.Attribute)
        and receiver.attr == "_observer"
        and isinstance(receiver.value, ast.Name)
        and receiver.value.id == "self"
    )


def _invalid_watch_schedule_receiver_violation(
    rel_path: str, lineno: int
) -> FseventsWatchViolation:
    """INV-6 failure: another scheduler cannot satisfy the monitor contract."""
    return FseventsWatchViolation(
        kind="invalid_watch_schedule_receiver",
        file_path=rel_path,
        line=lineno,
        message=(
            "the canonical watch must call self._observer.schedule(...) directly; "
            "do not hide the lifecycle-owned observer behind another receiver or dynamic lookup"
        ),
    )


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
    if not _is_canonical_schedule_receiver(schedule_call):
        return [_invalid_watch_schedule_receiver_violation(rel_path, schedule_call.lineno)]

    parents: dict[ast.AST, ast.AST] = _build_parent_map(tree)
    ancestors: list[ast.AST] = _ancestors(schedule_call, parents)

    if _has_loop_ancestor(ancestors):
        return [_loop_ancestor_violation(rel_path, schedule_call.lineno)]

    enclosing_function: ast.FunctionDef | ast.AsyncFunctionDef | None = _nearest_enclosing_function(
        ancestors
    )
    if enclosing_function is None or enclosing_function.name != "start":
        actual_name: str = enclosing_function.name if enclosing_function is not None else "<module>"
        return [_wrong_enclosing_function_violation(rel_path, schedule_call.lineno, actual_name)]

    return []


def _unowned_watch_observer_violation(rel_path: str, lineno: int) -> FseventsWatchViolation:
    """INV-7 failure: only the lifecycle owner may create a watch observer."""
    return FseventsWatchViolation(
        kind="unowned_watch_observer",
        file_path=rel_path,
        line=lineno,
        message=(
            "watchdog Observer() construction is outside the lifecycle-owned "
            "WorkspaceMonitor; route workspace events through "
            "WorkspaceMonitor.start() instead of creating another event source"
        ),
    )


def _unowned_watch_schedule_violation(
    rel_path: str, lineno: int, *, dynamic_getattr: bool = False
) -> FseventsWatchViolation:
    """INV-5 failure: a production module attempts to own a watch schedule."""
    if dynamic_getattr:
        message = (
            "dynamic getattr attribute name cannot prove this is not observer.schedule; "
            "route workspace events through the lifecycle-owned WorkspaceMonitor.start() "
            "instead of adding a potentially overlapping watch"
        )
    else:
        message = (
            "observer.schedule(...) is outside the lifecycle-owned "
            "WorkspaceMonitor.start(); route workspace events through the "
            "canonical monitor instead of adding an overlapping watch"
        )
    return FseventsWatchViolation(
        kind="unowned_watch_schedule",
        file_path=rel_path,
        line=lineno,
        message=message,
    )


def _aliased_watch_schedule_violation(rel_path: str, lineno: int) -> FseventsWatchViolation:
    """INV-1 failure: a bound ``.schedule`` method hides its call site."""
    return FseventsWatchViolation(
        kind="aliased_watch_schedule",
        file_path=rel_path,
        line=lineno,
        message=(
            "observer.schedule must be invoked directly inside WorkspaceMonitor.start(); "
            "a bound schedule alias can hide an overlapping watch from the fail-closed audit"
        ),
    )


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


def _unowned_schedule_violations(package_root: Path) -> list[FseventsWatchViolation]:
    """Reject watch schedules outside the lifecycle-owned monitor module.

    Only generated bytecode caches are excluded; every Python module is
    automatically in scope. Unreadable or
    unparsable source fails closed because the watch audit cannot prove it
    contains no raw schedule call.
    """
    violations: list[FseventsWatchViolation] = []
    for module_path in sorted(package_root.rglob("*.py")):
        rel_path = module_path.relative_to(package_root).as_posix()
        if rel_path == _WORKSPACE_MONITOR_MODULE:
            continue
        if any(part in _EXCLUDED_DIRECTORY_NAMES for part in module_path.parts):
            continue
        try:
            source = module_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            violations.append(
                FseventsWatchViolation(
                    kind="unreadable_production_module",
                    file_path=rel_path,
                    line=0,
                    message=(
                        "production module could not be read; restore readable source so "
                        "the package-wide watch ownership audit can fail closed"
                    ),
                )
            )
            continue
        # Every discovered production module is parsed. A syntactically invalid
        # new module must fail closed even when it has no textual ``.schedule``
        # marker; otherwise it could conceal an unrecognized watch owner.
        try:
            tree = ast.parse(source, filename=str(module_path))
        except (SyntaxError, ValueError) as exc:
            violations.append(
                FseventsWatchViolation(
                    kind="invalid_production_module",
                    file_path=rel_path,
                    line=exc.lineno if isinstance(exc, SyntaxError) and exc.lineno else 0,
                    message=(
                        "production module could not be parsed; restore valid source so "
                        "the package-wide watch ownership audit can fail closed"
                    ),
                )
            )
            continue
        # Most production modules cannot own a watch. Parse every module first
        # so malformed source still fails closed. Observer construction is also
        # ownership: a helper that creates an observer before scheduling it
        # elsewhere would otherwise evade the schedule-only check.
        violations.extend(
            _unowned_watch_observer_violation(rel_path, line)
            for line in _observer_constructor_lines(tree)
        )
        # Direct and literal schedule forms are cheap to skip when their marker
        # is absent, but every module still checks nonliteral ``getattr`` because
        # AST cannot prove that such a lookup is unrelated to scheduling.
        if "schedule" in source:
            violations.extend(
                _unowned_watch_schedule_violation(rel_path, call.lineno)
                for call in _find_schedule_calls(tree)
            )
            violations.extend(
                _unowned_watch_schedule_violation(rel_path, alias.lineno)
                for alias in _find_schedule_aliases(tree)
            )
        for node in ast.walk(tree):
            line = _nonliteral_observer_getattr_invocation_line(node)
            if line is not None:
                violations.append(
                    _unowned_watch_schedule_violation(rel_path, line, dynamic_getattr=True)
                )
    return violations


def audit_fsevents_watch_consolidation(
    package_root: Path,
) -> list[FseventsWatchViolation]:
    """Walk the production source tree and return all violations.

    Scans every production module for raw schedules and enforces INV-1..INV-5
    on the canonical ``_workspace.py`` owner. A missing package root produces
    a violation so programmatic callers also fail closed.
    """
    if not package_root.is_dir():
        return [
            FseventsWatchViolation(
                kind="missing_package_root",
                file_path=str(package_root),
                line=0,
                message=(
                    "package root does not exist or is not a directory; cannot prove "
                    "package-wide watch ownership, so the audit must fail closed"
                ),
            )
        ]

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
        return [
            FseventsWatchViolation(
                kind="unreadable_workspace_module",
                file_path=rel_path,
                line=0,
                message=(
                    "canonical workspace monitor source could not be read; restore readable "
                    "source so the package-wide watch ownership audit can fail closed"
                ),
            )
        ]

    # Parse before looking for a schedule call. Otherwise malformed source that
    # happens not to contain the textual marker would be misclassified as a
    # missing watch and evade the audit's fail-closed invalid-source diagnosis.

    return _check_module(module_path, rel_path, source) + _unowned_schedule_violations(package_root)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.  Returns 0 when clean, 1 on violations, 2 on bad root."""
    if argv is None:
        argv = sys.argv[1:]

    package_root: Path = Path(argv[0]) if argv else Path(__file__).parent.parent

    if not package_root.is_dir():
        print(f"Package root not found: {package_root}", file=sys.stderr)
        return 2

    violations: list[FseventsWatchViolation] = audit_fsevents_watch_consolidation(package_root)

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
