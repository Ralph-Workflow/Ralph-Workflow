"""Duplicate-keyword forwarding audit.

Bans the wrapper shape that silently breaks every call through a seam::

    def wrapper(a, b, **opts):
        return inner(a, b, some_hook=DEFAULT, **opts)

``some_hook`` is not a parameter of ``wrapper``, so a caller naming it lands
it in ``opts`` -- and the forwarded ``**opts`` then collides with the explicit
keyword. Python raises ``TypeError: <inner>() got multiple values for keyword
argument 'some_hook'`` at call time, never at import time, so the shape
type-checks, passes any test that does not exercise that exact caller, and
fails only in production.

This is not hypothetical: ``ralph/pipeline/runner.py::execute_commit_effect``
hardcoded ``has_commit_work_fn`` / ``has_residual_work_fn`` alongside
``**opts`` while its only production caller
(``_execute_commit_effect_from_deps``) named ``has_residual_work_fn``. Every
commit the pipeline attempted raised ``TypeError``, which the recovery
classifier could only label ``ambiguous``; the ``development_commit`` phase
routed to the failed terminal, re-entered itself, and looped without ever
producing a commit. The unit tests all called ``execute_commit_effect``
directly and never crossed the seam.

The fix, and the shape this audit enforces, is to seed the catch-all instead
of forwarding past it::

    def wrapper(a, b, **opts):
        opts.setdefault("some_hook", DEFAULT)
        return inner(a, b, **opts)

An explicit keyword is accepted only when it genuinely cannot collide:

  * It names a plain or keyword-only parameter of the enclosing function. A
    caller passing that name binds it to the parameter, so it never reaches
    the catch-all dict.

    Positional-only parameters do NOT qualify: ``def w(a, /, **opts)`` still
    routes ``w(1, a=2)`` into ``opts``. Neither do the ``*args`` and
    ``**kwargs`` names themselves -- ``w(args=5)`` and ``w(opts=5)`` both land
    in the catch-all.

  * The enclosing function rejects the name before forwarding, with a guard
    whose body raises::

        if "buffering" in kwargs:
            raise TypeError("callers must NOT pass buffering")

    which turns the collision into an explicit, well-described error.
    ``ralph/logging.py::_add_buffered_file_sink`` uses this form. A guard that
    only logs does not qualify, because the call still raises.

Forwarding is detected through derived unpacking too: ``**{**opts}`` and
``**dict(opts)`` collide exactly as ``**opts`` does, so any ``**`` argument
whose expression mentions the catch-all counts as a forward.

Nested scopes are attributed correctly. ``audit_function`` descends into
nested ``def``/``lambda`` bodies while extending the safe set with the names
those scopes bind, and stops at any scope that rebinds the catch-all name --
that scope is audited in its own right.

Usage:
    python -m ralph.testing.audit_kwargs_forwarding [root1 root2 ...]

Exit codes:
  0 = clean.
  1 = violations found.
  2 = a named root does not exist.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

from ralph.testing._audit_parse_error import AuditParseError

#: Both the shipped package and its tests are gated: a helper in ``tests/``
#: carrying the banned shape is a landmine for the next author of that helper.
DEFAULT_ROOTS: tuple[str, ...] = ("ralph", "tests")

_Scope = ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda

#: Node types that open a new binding scope, as a tuple for ``isinstance``.
_SCOPE_NODES: tuple[type[_Scope], ...] = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


@dataclass(frozen=True)
class Violation:
    """One forwarded call that can collide with its own catch-all."""

    path: str
    lineno: int
    function: str
    catchall: str
    keyword: str

    def __str__(self) -> str:
        return (
            f"{self.path}:{self.lineno} {self.function}() forwards **{self.catchall} "
            f"while also passing {self.keyword}=...; a caller naming "
            f"'{self.keyword}' raises TypeError. Use "
            f"{self.catchall}.setdefault({self.keyword!r}, ...) instead."
        )


def _bindable_parameter_names(scope: _Scope) -> set[str]:
    """Return the names a caller can bind by keyword on ``scope``.

    Only plain and keyword-only parameters qualify. Positional-only names, the
    ``*args`` name, and the ``**kwargs`` name are all still routable INTO the
    catch-all by a keyword caller, so reusing them as explicit keywords beside
    a forward is exactly as hazardous as any other name.
    """
    args = scope.args
    return {arg.arg for arg in (*args.args, *args.kwonlyargs)}


def _rejecting_guard_names(scope: _Scope, catchall: str) -> set[str]:
    """Return keyword names the scope raises on before forwarding.

    Recognises ``if "<name>" in <catchall>: ... raise ...``. The raise is
    required: a guard that only logs leaves the collision intact.
    """
    guarded: set[str] = set()
    for node in ast.walk(scope):
        if not isinstance(node, ast.If):
            continue
        if not any(isinstance(inner, ast.Raise) for inner in ast.walk(node)):
            continue
        for test in ast.walk(node.test):
            if not isinstance(test, ast.Compare) or len(test.ops) != 1:
                continue
            if not isinstance(test.ops[0], ast.In):
                continue
            left = test.left
            right = test.comparators[0]
            if not isinstance(left, ast.Constant) or not isinstance(left.value, str):
                continue
            if isinstance(right, ast.Name) and right.id == catchall:
                guarded.add(left.value)
    return guarded


def _mentions(node: ast.AST, name: str) -> bool:
    return any(isinstance(child, ast.Name) and child.id == name for child in ast.walk(node))


def _forwards_catchall(call: ast.Call, catchall: str) -> bool:
    """Report whether ``call`` unpacks the catch-all, directly or derived.

    ``**opts``, ``**{**opts}`` and ``**dict(opts)`` all deliver the caller's
    keys to the callee, so all three collide with an explicit keyword.
    """
    return any(
        keyword.arg is None and _mentions(keyword.value, catchall) for keyword in call.keywords
    )


def _walk_scope(node: ast.AST, catchall: str, safe: frozenset[str]) -> list[tuple[ast.Call, frozenset[str]]]:
    """Collect calls reachable from ``node``, carrying each one's safe-name set.

    Descending into a nested ``def``/``lambda`` extends the safe set with the
    names that scope binds, so a nested helper that declares the keyword is not
    reported against its parent. A scope that rebinds the catch-all name is
    skipped: it shadows the name, and its own pass audits it.
    """
    found: list[tuple[ast.Call, frozenset[str]]] = []
    if isinstance(node, _SCOPE_NODES):
        scope = node
        bound = _bindable_parameter_names(scope)
        args = scope.args
        shadowing = {arg.arg for arg in args.posonlyargs} | bound
        if args.vararg is not None:
            shadowing.add(args.vararg.arg)
        if args.kwarg is not None:
            shadowing.add(args.kwarg.arg)
        if catchall in shadowing:
            return found
        safe = safe | bound
    if isinstance(node, ast.Call):
        found.append((node, safe))
    for child in ast.iter_child_nodes(node):
        found.extend(_walk_scope(child, catchall, safe))
    return found


def audit_function(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    path: str,
) -> list[Violation]:
    """Return every colliding explicit keyword in one function's forwarded calls."""
    if fn.args.kwarg is None:
        return []
    catchall = fn.args.kwarg.arg
    base = frozenset(_bindable_parameter_names(fn) | _rejecting_guard_names(fn, catchall))
    violations: list[Violation] = []
    for child in ast.iter_child_nodes(fn):
        for call, safe in _walk_scope(child, catchall, base):
            if not _forwards_catchall(call, catchall):
                continue
            for keyword in call.keywords:
                if keyword.arg is None or keyword.arg in safe:
                    continue
                violations.append(
                    Violation(
                        path=path,
                        lineno=call.lineno,
                        function=fn.name,
                        catchall=catchall,
                        keyword=keyword.arg,
                    )
                )
    return violations


def audit_source(source: str, path: str) -> list[Violation]:
    """Return every violation in one module's source text.

    Raises:
        AuditParseError: when the source does not parse. An unparseable file
            must never be reported as clean.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise AuditParseError(f"{path}: {exc}") from exc
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            violations.extend(audit_function(node, path))
    return violations


def audit_tree(
    package_root: Path,
    roots: tuple[str, ...] = DEFAULT_ROOTS,
) -> tuple[list[Violation], int]:
    """Return violations under ``package_root`` and the number of files scanned.

    The file count is part of the result so a caller can tell a genuinely
    clean tree from a misresolved root that scanned nothing.

    Raises:
        FileNotFoundError: when a named root is not a directory. Silently
            skipping it would report a vacuous clean run.
    """
    violations: list[Violation] = []
    scanned = 0
    for root in roots:
        base = package_root / root
        if not base.is_dir():
            raise FileNotFoundError(f"audit root is not a directory: {base}")
        for path in sorted(base.rglob("*.py")):
            try:
                label = str(path.relative_to(package_root))
            except ValueError:
                label = str(path)
            violations.extend(audit_source(path.read_text(encoding="utf-8"), label))
            scanned += 1
    return violations, scanned


def main(argv: list[str] | None = None) -> int:
    """Run the audit over the repository and report violations."""
    args = list(sys.argv[1:] if argv is None else argv)
    package_root = Path.cwd()
    roots = tuple(args) if args else DEFAULT_ROOTS
    try:
        violations, scanned = audit_tree(package_root, roots)
    except FileNotFoundError as exc:
        print(exc)
        return 2
    except AuditParseError as exc:
        print(f"Could not parse a file under audit: {exc}")
        return 1
    if not violations:
        print(
            f"No duplicate-keyword forwarding in {scanned} file(s) under {', '.join(roots)}."
        )
        return 0
    print(f"DUPLICATE-KEYWORD FORWARDING VIOLATIONS: {len(violations)} in {scanned} file(s)")
    print("=" * 72)
    for violation in violations:
        print(f"  {violation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
