from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Final

from ralph._typed_attrs import read_attr

if TYPE_CHECKING:
    from collections.abc import Callable

_B12_DISPATCH_CALL_NAME: Final = "run_agent_attempt"
_B12_PRODUCTION_PREFIX: Final = "ralph/"
_B12_EXCLUDED_PREFIXES: Final = ("ralph/testing/",)
_B12_FUNCTION_TYPES: Final = (ast.FunctionDef, ast.AsyncFunctionDef)


def _call_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _violation(path: str, node: ast.AST, rule: str, reason: str) -> str:
    return f"{path}:{read_attr(node, 'lineno', 1)} {rule} {reason}"


def _parent_map(tree: ast.Module) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


def _enclosing_function_name(node: ast.AST, parents: dict[int, ast.AST]) -> str | None:
    current = parents.get(id(node))
    while current is not None:
        if isinstance(current, _B12_FUNCTION_TYPES):
            return current.name
        current = parents.get(id(current))
    return None


def b12_violations(
    relative_path: str,
    tree: ast.Module,
    source: str,
    nodes: list[ast.AST],
    allowlist_justification: Callable[[str, str | None], str | None],
) -> list[str]:
    if not relative_path.startswith(_B12_PRODUCTION_PREFIX):
        return []
    if relative_path.startswith(_B12_EXCLUDED_PREFIXES):
        return []
    if _B12_DISPATCH_CALL_NAME not in source:
        return []
    parents = _parent_map(tree)
    violations: list[str] = []
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        if _call_name(node.func) != _B12_DISPATCH_CALL_NAME:
            continue
        function_name = _enclosing_function_name(node, parents)
        if allowlist_justification(relative_path, function_name) is not None:
            continue
        violations.append(
            _violation(
                relative_path,
                node,
                "B12",
                "agent attempt dispatch outside the registered wrapper allowlist",
            )
        )
    return violations
