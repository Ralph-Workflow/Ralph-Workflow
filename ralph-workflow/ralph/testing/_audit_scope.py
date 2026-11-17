from __future__ import annotations

import ast
from typing import TYPE_CHECKING, TypeIs

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "build_parent_map",
    "build_parent_map_from_nodes",
    "is_assign_binding",
    "is_assign_or_ann",
    "is_function_def",
    "is_function_or_class",
    "is_loop",
    "is_mutable_literal",
    "is_sequence_display",
    "qualified_scope",
]

MODULE_SCOPE = "<module>"


def is_function_def(node: ast.AST) -> TypeIs[ast.FunctionDef | ast.AsyncFunctionDef]:
    match node:
        case ast.FunctionDef() | ast.AsyncFunctionDef():
            return True
        case _:
            return False


def is_function_or_class(
    node: ast.AST,
) -> TypeIs[ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef]:
    match node:
        case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.ClassDef():
            return True
        case _:
            return False


def is_assign_or_ann(node: ast.AST) -> TypeIs[ast.Assign | ast.AnnAssign]:
    match node:
        case ast.Assign() | ast.AnnAssign():
            return True
        case _:
            return False


def is_assign_binding(
    node: ast.AST,
) -> TypeIs[ast.Assign | ast.NamedExpr | ast.AnnAssign]:
    match node:
        case ast.Assign() | ast.NamedExpr() | ast.AnnAssign():
            return True
        case _:
            return False


def is_sequence_display(node: ast.AST) -> TypeIs[ast.Tuple | ast.List | ast.Set]:
    match node:
        case ast.Tuple() | ast.List() | ast.Set():
            return True
        case _:
            return False


def is_mutable_literal(node: ast.AST) -> TypeIs[ast.Dict | ast.List | ast.Set]:
    match node:
        case ast.Dict() | ast.List() | ast.Set():
            return True
        case _:
            return False


def is_loop(node: ast.AST) -> TypeIs[ast.For | ast.AsyncFor | ast.While]:
    match node:
        case ast.For() | ast.AsyncFor() | ast.While():
            return True
        case _:
            return False


def build_parent_map_from_nodes(nodes: Iterable[ast.AST]) -> dict[ast.AST, ast.AST]:
    return {child: node for node in nodes for child in ast.iter_child_nodes(node)}


def build_parent_map(tree_or_nodes: ast.AST | Iterable[ast.AST]) -> dict[ast.AST, ast.AST]:
    if isinstance(tree_or_nodes, ast.AST):
        return build_parent_map_from_nodes(ast.walk(tree_or_nodes))
    return build_parent_map_from_nodes(tree_or_nodes)


def qualified_scope(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str:
    names: list[str] = []
    current: ast.AST | None = node
    while current is not None:
        if is_function_or_class(current):
            names.append(current.name)
        current = parents.get(current)
    names.reverse()
    if not names:
        return MODULE_SCOPE
    return ".".join(names)
