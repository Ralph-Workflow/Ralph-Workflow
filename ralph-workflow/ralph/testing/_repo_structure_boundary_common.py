from __future__ import annotations

import ast
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

RULE_PAIRINGS: Final[tuple[tuple[str, str], ...]] = (
    ("B1", "U6"),
    ("B2", "AC16"),
    ("B3", "AC16"),
    ("B4", "U3a/U10"),
    ("B5", "U6"),
    ("B6", "AC4"),
    ("B7", "U6"),
    ("B8", "AC4"),
    ("B9", "AC3/AC15"),
    ("B9b", "AC3/AC15"),
    ("B10", "AC13"),
    ("B11", "AC-matrix-completeness"),
)
_PAIRING_BY_RULE: Final[Mapping[str, str]] = MappingProxyType(dict(RULE_PAIRINGS))


def _violation(path: str, node: ast.AST, rule: str, reason: str) -> str:
    if rule not in _PAIRING_BY_RULE:
        raise ValueError(f"undeclared boundary rule id: {rule}")
    return f"{path}:{getattr(node, 'lineno', 1)} {rule} {reason}"


def _call_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_call(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Name) and node.func.id == name)
        or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
    )


def _target_root_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Attribute):
        return {target.attr}
    if isinstance(target, ast.Starred):
        return _target_root_names(target.value)
    if isinstance(target, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for element in target.elts:
            names.update(_target_root_names(element))
        return names
    return set()


def _type_checking_nodes(tree: ast.Module) -> set[int]:
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
            for child in node.body:
                guarded.update(id(item) for item in ast.walk(child))
    return guarded


def _parent_map(tree: ast.Module) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents
