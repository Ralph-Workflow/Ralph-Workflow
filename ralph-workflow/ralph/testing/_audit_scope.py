from __future__ import annotations

import ast
from collections.abc import Sequence

__all__ = ["build_parent_map", "qualified_scope"]

MODULE_SCOPE = "<module>"


def build_parent_map(tree_or_nodes: ast.AST | Sequence[ast.AST]) -> dict[ast.AST, ast.AST]:
    nodes = tree_or_nodes if isinstance(tree_or_nodes, Sequence) else ast.walk(tree_or_nodes)
    return {child: node for node in nodes for child in ast.iter_child_nodes(node)}


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
