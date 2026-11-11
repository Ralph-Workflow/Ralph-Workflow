from __future__ import annotations

import ast
import multiprocessing
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from ralph.testing._audit_parse_error import AuditParseError

DEFAULT_ROOTS: tuple[str, ...] = ("ralph", "tests")

_POOL_WORKER_CAP = 1

_PARALLEL_MIN_FILES = 16

_POOL_CONTEXT = multiprocessing.get_context("spawn")

_Scope = ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda

_SCOPE_NODES: tuple[type[_Scope], ...] = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


@dataclass(frozen=True)
class Violation:
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
    args = scope.args
    return {arg.arg for arg in (*args.args, *args.kwonlyargs)}


def _rejecting_guard_names(scope: _Scope, catchall: str) -> set[str]:
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
    return any(
        keyword.arg is None and _mentions(keyword.value, catchall) for keyword in call.keywords
    )


def _walk_scope(
    node: ast.AST, catchall: str, safe: frozenset[str]
) -> list[tuple[ast.Call, frozenset[str]]]:
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
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise AuditParseError(f"{path}: {exc}") from exc
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            violations.extend(audit_function(node, path))
    return violations


def _audit_file_payload(payload: tuple[str, str]) -> list[Violation]:
    label, source = payload
    return audit_source(source, label)


def audit_tree(
    package_root: Path,
    roots: tuple[str, ...] = DEFAULT_ROOTS,
) -> tuple[list[Violation], int]:
    payloads: list[tuple[str, str]] = []
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
            source = path.read_text(encoding="utf-8")
            if "**" in source:
                payloads.append((label, source))
            scanned += 1
    workers = min(_POOL_WORKER_CAP, len(payloads), os.cpu_count() or 1)
    violations: list[Violation] = []
    if workers == 1 or len(payloads) < _PARALLEL_MIN_FILES:
        for payload in payloads:
            violations.extend(_audit_file_payload(payload))
        return violations, scanned
    with ProcessPoolExecutor(max_workers=workers, mp_context=_POOL_CONTEXT) as pool:
        for result in pool.map(_audit_file_payload, payloads):
            violations.extend(result)
    return violations, scanned


def main(argv: list[str] | None = None) -> int:
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
        print(f"No duplicate-keyword forwarding in {scanned} file(s) under {', '.join(roots)}.")
        return 0
    print(f"DUPLICATE-KEYWORD FORWARDING VIOLATIONS: {len(violations)} in {scanned} file(s)")
    print("=" * 72)
    for violation in violations:
        print(f"  {violation}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
