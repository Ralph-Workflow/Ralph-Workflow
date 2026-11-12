from __future__ import annotations

import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["build_parent_map", "build_parent_map_from_nodes", "qualified_scope"]

MODULE_SCOPE = "<module>"


def build_parent_map_from_nodes(nodes: Iterable[ast.AST]) -> dict[ast.AST, ast.AST]:
    return {child: node for node in nodes for child in ast.iter_child_nodes(node)}


def build_parent_map(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    return build_parent_map_from_nodes(ast.walk(tree))


def qualified_scope(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    names: list[str] = []
    current: ast.AST | None = node
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(current.name)
        current = parents.get(current)
    names.reverse()
    if not names:
        return MODULE_SCOPE
    return ".".join(names)
