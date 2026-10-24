"""AST detectors for white-box coupling in Ralph test files."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection

WhiteboxCounts = dict[str, int]

_DUNDER_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "__all__",
        "__name__",
        "__class__",
        "__type__",
        "__module__",
        "__dict__",
        "__init__",
        "__enter__",
        "__exit__",
        "__doc__",
    }
)


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _is_monkeypatch_fixture_name(name: str) -> bool:
    return name == "monkeypatch" or name.startswith("monkeypatch")


def _collect_monkeypatch_names(tree: ast.AST) -> set[str]:
    """Collect names bound to pytest's monkeypatch fixture."""
    bound = {"monkeypatch"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            arguments = (
                list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
            )
            for argument in arguments:
                if _is_monkeypatch_fixture_name(argument.arg):
                    bound.add(argument.arg)

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not isinstance(value, ast.Name) or value.id not in bound:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in bound:
                    bound.add(target.id)
                    changed = True
    return bound


def _is_ralph_module(module: str) -> bool:
    return module == "ralph" or module.startswith("ralph.")


def _module_last_segment(module: str) -> str:
    return module.rsplit(".", 1)[-1]


def count_setattr_private(tree: ast.AST) -> int:
    """Count monkeypatch calls that target a private production attribute."""
    bound = _collect_monkeypatch_names(tree)
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if not isinstance(function, ast.Attribute) or function.attr != "setattr":
            continue
        receiver = function.value
        if not isinstance(receiver, ast.Name) or receiver.id not in bound:
            continue
        if not node.args:
            continue

        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            target = first.value.rsplit(".", 1)[-1]
            if target.startswith("_") and not _is_dunder(target):
                count += 1
                continue

        second_arguments = node.args[1:2]
        if not second_arguments:
            continue
        second = second_arguments[0]
        if (
            isinstance(second, ast.Constant)
            and isinstance(second.value, str)
            and second.value.startswith("_")
            and not _is_dunder(second.value)
        ):
            count += 1
    return count


def count_private_imports(tree: ast.AST) -> int:
    """Count imports that reach private ``ralph`` production internals."""
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if not _is_ralph_module(module):
                continue
            if _module_last_segment(module).startswith("_"):
                count += 1
            for alias in node.names:
                if alias.name.startswith("_") and alias.name != "*" and not _is_dunder(alias.name):
                    count += 1
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if not _is_ralph_module(alias.name):
                    continue
                if _module_last_segment(alias.name).startswith("_"):
                    count += 1
    return count


def _collect_ralph_module_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if _is_ralph_module(module):
                for alias in node.names:
                    names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _is_ralph_module(alias.name):
                    names.add(alias.asname or alias.name.split(".")[0])
    return names


def _collect_setattr_target_ids(tree: ast.AST, bound: set[str]) -> set[int]:
    targets: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if not isinstance(function, ast.Attribute) or function.attr != "setattr":
            continue
        receiver = function.value
        if not isinstance(receiver, ast.Name) or receiver.id not in bound:
            continue
        for argument in node.args[:2]:
            for descendant in ast.walk(argument):
                targets.add(id(descendant))
    return targets


def _collect_assert_node_ids(tree: ast.AST) -> set[int]:
    nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            for descendant in ast.walk(node):
                nodes.add(id(descendant))
    return nodes


def count_private_attrs(tree: ast.AST) -> int:
    """Count private attribute accesses in high-confidence test contexts."""
    bound = _collect_monkeypatch_names(tree)
    ralph_modules = _collect_ralph_module_names(tree)
    setattr_targets = _collect_setattr_target_ids(tree, bound)
    assert_nodes = _collect_assert_node_ids(tree)

    count = 0
    seen: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        attribute = node.attr
        if not attribute.startswith("_") or attribute in _DUNDER_ATTRIBUTES:
            continue
        if _is_dunder(attribute):
            continue
        identity = id(node)
        if identity in seen:
            continue
        in_assert = identity in assert_nodes
        is_setattr_target = identity in setattr_targets
        is_ralph_module_read = isinstance(node.value, ast.Name) and node.value.id in ralph_modules
        if in_assert or is_setattr_target or is_ralph_module_read:
            seen.add(identity)
            count += 1
    return count


def collect_whitebox_counts(tree: ast.AST) -> WhiteboxCounts:
    """Return all white-box detector counts for one parsed test module."""
    return {
        "setattr_private": count_setattr_private(tree),
        "private_import": count_private_imports(tree),
        "private_attr": count_private_attrs(tree),
    }


def _dotted_expression(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        receiver = _dotted_expression(node.value)
        if receiver is None:
            return None
        return f"{receiver}.{node.attr}"
    return None


def canonical_patch_target(receiver: str, attribute: str) -> str:
    """Return the canonical dotted target for a patched attribute."""
    if receiver == "Path":
        receiver = "pathlib.Path"
    return f"{receiver}.{attribute}"


def canonical_target_string(target: str) -> str:
    """Return the canonical spelling of a string patch target."""
    if target == "open":
        return "builtins.open"
    if target.startswith("Path."):
        return f"pathlib.{target}"
    return target


def _is_known_patch_call(function_name: str, patterns: Collection[str]) -> bool:
    normalized = {pattern.removesuffix("(") for pattern in patterns}
    return function_name in normalized


def collect_patched_targets(
    tree: ast.AST,
    monkeypatch_patterns: Collection[str],
) -> set[str]:
    """Collect exact dotted targets patched in ``tree``."""
    bound = _collect_monkeypatch_names(tree)
    targets: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function_name = _dotted_expression(node.func)
        if function_name is None:
            continue

        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "setattr"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in bound
        ):
            if node.args and isinstance(node.args[0], ast.Constant):
                first_node = node.args[0]
                if isinstance(first_node.value, str):
                    targets.add(canonical_target_string(first_node.value))
            attribute_arguments = node.args[1:2]
            if not attribute_arguments or not isinstance(attribute_arguments[0], ast.Constant):
                continue
            attribute_constant = attribute_arguments[0]
            if not isinstance(attribute_constant.value, str):
                continue
            receiver = _dotted_expression(node.args[0])
            if receiver is not None:
                targets.add(canonical_patch_target(receiver, attribute_constant.value))
            continue

        if _is_known_patch_call(function_name, monkeypatch_patterns) and node.args:
            patch_target_node = node.args[0]
            if isinstance(patch_target_node, ast.Constant) and isinstance(
                patch_target_node.value, str
            ):
                targets.add(canonical_target_string(patch_target_node.value))
            continue

        receiver_arguments = node.args[:1]
        attribute_arguments = node.args[1:2]
        if function_name.endswith(".patch.object") and receiver_arguments and attribute_arguments:
            receiver = _dotted_expression(receiver_arguments[0])
            attribute_expression = attribute_arguments[0]
            if (
                receiver is not None
                and isinstance(attribute_expression, ast.Constant)
                and isinstance(attribute_expression.value, str)
            ):
                targets.add(canonical_patch_target(receiver, attribute_expression.value))
    return targets
