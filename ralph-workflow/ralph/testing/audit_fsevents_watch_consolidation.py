from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


_WORKSPACE_MONITOR_MODULE: str = "agents/invoke/_workspace.py"

_EXCLUDED_DIRECTORY_NAMES: frozenset[str] = frozenset({"__pycache__"})
_GETATTR_ATTRIBUTE_POSITION: int = 1
_MIN_GETATTR_ARGUMENTS: int = _GETATTR_ATTRIBUTE_POSITION + 1


@dataclass(frozen=True)
class FseventsWatchViolation:
    kind: str
    file_path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.file_path}:{self.line}: [{self.kind}] {self.message}"


def _build_parent_map(nodes: Sequence[ast.AST]) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for node in nodes:
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _ancestors(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> list[ast.AST]:
    chain: list[ast.AST] = []
    current: ast.AST | None = parents.get(node)
    while current is not None:
        chain.append(current)
        current = parents.get(current)
    return chain


def _is_dynamic_schedule_lookup(node: ast.AST) -> bool:
    match node:
        case ast.Call(
            func=ast.Name(id="getattr"),
            args=[_, ast.Constant(value="schedule"), *_],
        ):
            return True
        case _:
            return False


def _is_observer_receiver(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id.endswith("observer")
    return isinstance(node, ast.Attribute) and node.attr.endswith("observer")


def _nonliteral_observer_getattr_invocation_line(node: ast.AST) -> int | None:
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


def _observer_constructor_aliases(nodes: Sequence[ast.AST]) -> frozenset[str]:
    aliases: set[str] = set()
    for node in nodes:
        if not isinstance(node, ast.ImportFrom) or node.module != "watchdog.observers":
            continue
        aliases.update(
            imported.asname or imported.name
            for imported in node.names
            if imported.name == "Observer"
        )
    return frozenset(aliases)


def _observer_constructor_lines(nodes: Sequence[ast.AST]) -> list[int]:
    aliases = _observer_constructor_aliases(nodes)
    return [
        node.lineno
        for node in nodes
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in aliases
    ]


def _find_schedule_calls(nodes: Sequence[ast.AST]) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute) and func.attr == "schedule"
        ) or _is_dynamic_schedule_lookup(func):
            calls.append(node)
    return calls


def _find_schedule_aliases(nodes: Sequence[ast.AST]) -> list[ast.expr]:
    aliases: list[ast.expr] = []
    for node in nodes:
        if not isinstance(node, (ast.Assign, ast.NamedExpr, ast.AnnAssign)):
            continue
        value = node.value
        if (isinstance(value, ast.Attribute) and value.attr == "schedule") or (
            isinstance(value, ast.Call) and _is_dynamic_schedule_lookup(value)
        ):
            aliases.append(value)
    return aliases


def _has_recursive_true_kwarg(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg != "recursive":
            continue
        value = kw.value
        return isinstance(value, ast.Constant) and value.value is True
    return False


def _has_loop_ancestor(ancestors: list[ast.AST]) -> bool:
    return any(isinstance(ancestor, (ast.For, ast.AsyncFor, ast.While)) for ancestor in ancestors)


def _nearest_enclosing_function(
    ancestors: list[ast.AST],
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for ancestor in ancestors:
        if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return ancestor
    return None


def _check_module(
    module_path: Path,
    rel_path: str,
    source: str,
) -> list[FseventsWatchViolation]:
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

    nodes = list(ast.walk(tree))
    schedule_aliases: list[ast.expr] = _find_schedule_aliases(nodes)
    if schedule_aliases:
        return [
            _aliased_watch_schedule_violation(rel_path, alias.lineno) for alias in schedule_aliases
        ]

    schedule_calls: list[ast.Call] = _find_schedule_calls(nodes)
    invariants_violations: list[FseventsWatchViolation] = _check_schedule_call_invariants(
        rel_path, schedule_calls
    )
    if invariants_violations or not schedule_calls:
        return invariants_violations

    return _check_schedule_call_location(rel_path, nodes, schedule_calls[0])


def _check_schedule_call_invariants(
    rel_path: str,
    schedule_calls: list[ast.Call],
) -> list[FseventsWatchViolation]:
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
    nodes: Sequence[ast.AST],
    schedule_call: ast.Call,
) -> list[FseventsWatchViolation]:
    if not _is_canonical_schedule_receiver(schedule_call):
        return [_invalid_watch_schedule_receiver_violation(rel_path, schedule_call.lineno)]

    parents: dict[ast.AST, ast.AST] = _build_parent_map(nodes)
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
        if "Observer" not in source and "schedule" not in source and "getattr" not in source:
            continue
        nodes = list(ast.walk(tree))
        violations.extend(
            _unowned_watch_observer_violation(rel_path, line)
            for line in _observer_constructor_lines(nodes)
        )
        if "schedule" in source:
            violations.extend(
                _unowned_watch_schedule_violation(rel_path, call.lineno)
                for call in _find_schedule_calls(nodes)
            )
            violations.extend(
                _unowned_watch_schedule_violation(rel_path, alias.lineno)
                for alias in _find_schedule_aliases(nodes)
            )
        for node in nodes:
            line = _nonliteral_observer_getattr_invocation_line(node)
            if line is not None:
                violations.append(
                    _unowned_watch_schedule_violation(rel_path, line, dynamic_getattr=True)
                )
    return violations


def audit_fsevents_watch_consolidation(
    package_root: Path,
) -> list[FseventsWatchViolation]:
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

    return _check_module(module_path, rel_path, source) + _unowned_schedule_violations(package_root)


def main(argv: Sequence[str] | None = None) -> int:
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
