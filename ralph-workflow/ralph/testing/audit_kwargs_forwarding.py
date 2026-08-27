"""Duplicate-keyword forwarding audit.

Bans the wrapper shape that silently breaks every call through a seam:

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

An explicit keyword is accepted when it CANNOT collide, i.e. when either:

  * it names a parameter of the enclosing function (positional, keyword-only,
    or the catch-all itself) -- a caller passing that name binds it to the
    parameter, so it can never reach the catch-all dict; or
  * the enclosing function guards the name, e.g.
    ``if "buffering" in kwargs: raise TypeError(...)``, which turns the
    collision into an explicit, well-described error before the forward.
    ``ralph/logging.py::_add_buffered_file_sink`` uses this form.

Usage:
    python -m ralph.testing.audit_kwargs_forwarding [root1 root2 ...]

Exit codes:
  0 = clean.
  1 = violations found.
  2 = root not found.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ROOTS: tuple[str, ...] = ("ralph",)


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


def _bound_parameter_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Return every name the enclosing signature binds directly.

    A caller cannot route these into the catch-all: Python binds them to the
    parameter instead, so an explicit keyword reusing the name never collides.
    """
    args = fn.args
    names = {arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    if args.vararg is not None:
        names.add(args.vararg.arg)
    if args.kwarg is not None:
        names.add(args.kwarg.arg)
    return names


def _guarded_names(fn: ast.FunctionDef | ast.AsyncFunctionDef, catchall: str) -> set[str]:
    """Return keyword names the function rejects before forwarding.

    Recognises the ``if "<name>" in <catchall>:`` membership test, the form
    used to turn a would-be collision into an explicit error.
    """
    guarded: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        if not isinstance(node.ops[0], ast.In):
            continue
        left = node.left
        right = node.comparators[0]
        if not isinstance(left, ast.Constant) or not isinstance(left.value, str):
            continue
        if isinstance(right, ast.Name) and right.id == catchall:
            guarded.add(left.value)
    return guarded


def _forwards_catchall(call: ast.Call, catchall: str) -> bool:
    return any(
        keyword.arg is None
        and isinstance(keyword.value, ast.Name)
        and keyword.value.id == catchall
        for keyword in call.keywords
    )


def audit_function(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    path: str,
) -> list[Violation]:
    """Return every colliding explicit keyword in one function's forwarded calls."""
    if fn.args.kwarg is None:
        return []
    catchall = fn.args.kwarg.arg
    safe = _bound_parameter_names(fn) | _guarded_names(fn, catchall)
    violations: list[Violation] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call) or not _forwards_catchall(node, catchall):
            continue
        for keyword in node.keywords:
            if keyword.arg is None or keyword.arg in safe:
                continue
            violations.append(
                Violation(
                    path=path,
                    lineno=node.lineno,
                    function=fn.name,
                    catchall=catchall,
                    keyword=keyword.arg,
                )
            )
    return violations


def audit_source(source: str, path: str) -> list[Violation]:
    """Return every violation in one module's source text."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            violations.extend(audit_function(node, path))
    return violations


def audit_tree(package_root: Path, roots: tuple[str, ...] = DEFAULT_ROOTS) -> list[Violation]:
    """Return every violation under ``package_root`` for the named roots."""
    violations: list[Violation] = []
    for root in roots:
        base = package_root / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            violations.extend(
                audit_source(path.read_text(encoding="utf-8"), str(path.relative_to(package_root)))
            )
    return violations


def main(argv: list[str] | None = None) -> int:
    """Run the audit over the repository and report violations."""
    args = list(sys.argv[1:] if argv is None else argv)
    package_root = Path.cwd()
    roots = tuple(args) if args else DEFAULT_ROOTS
    for root in roots:
        if not (package_root / root).is_dir():
            print(f"Root not found: {package_root / root}")
            return 2
    violations = audit_tree(package_root, roots)
    if not violations:
        print(f"No duplicate-keyword forwarding found under {', '.join(roots)}.")
        return 0
    print(f"DUPLICATE-KEYWORD FORWARDING VIOLATIONS: {len(violations)}")
    print("=" * 72)
    for violation in violations:
        print(f"  {violation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
