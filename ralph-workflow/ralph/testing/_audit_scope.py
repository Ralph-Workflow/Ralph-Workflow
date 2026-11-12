from __future__ import annotations

import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["build_parent_map", "qualified_scope"]

MODULE_SCOPE = "<module>"


def build_parent_map(
    tree: ast.Module, nodes: Sequence[ast.AST] | None = None
) -> dict[ast.AST, ast.AST]:
    collected = list(ast.walk(tree)) if nodes is None else nodes
    return {child: node for node in collected for child in ast.iter_child_nodes(node)}


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
