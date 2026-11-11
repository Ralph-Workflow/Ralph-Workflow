from __future__ import annotations

import ast
import inspect
from importlib import import_module

import pytest

BACKEND_MODULES: list[str] = [
    "ralph.mcp.websearch.backends.brave",
    "ralph.mcp.websearch.backends.searxng",
    "ralph.mcp.websearch.backends.ddgs",
    "ralph.mcp.websearch.backends.exa",
    "ralph.mcp.websearch.backends.tavily",
]

HTTP_BACKEND_MODULES: list[str] = [
    "ralph.mcp.websearch.backends.brave",
    "ralph.mcp.websearch.backends.searxng",
]

SDK_BACKEND_MODULES: list[str] = [
    "ralph.mcp.websearch.backends.ddgs",
    "ralph.mcp.websearch.backends.exa",
    "ralph.mcp.websearch.backends.tavily",
]


def _source_for(module_name: str) -> str:
    module = import_module(module_name)
    return inspect.getsource(module)


def _tree_for(module_name: str) -> ast.Module:
    return ast.parse(_source_for(module_name))


@pytest.mark.parametrize("module_name", BACKEND_MODULES)
def test_no_backend_declares_numeric_timeout_seconds_literal(module_name: str) -> None:
    tree = _tree_for(module_name)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id != "_TIMEOUT_SECONDS":
                continue
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, (int, float)):
                pytest.fail(
                    f"{module_name}:{node.lineno}: re-introduced hard-coded "
                    f"_TIMEOUT_SECONDS = {value.value!r} (must source from "
                    f"ralph.timeout_defaults.WEBSEARCH_BACKEND_TIMEOUT_SECONDS)"
                )


@pytest.mark.parametrize("module_name", HTTP_BACKEND_MODULES)
def test_http_backends_import_central_timeout(module_name: str) -> None:
    source = _source_for(module_name)
    assert "from ralph.timeout_defaults import WEBSEARCH_BACKEND_TIMEOUT_SECONDS" in source, (
        f"{module_name} must import WEBSEARCH_BACKEND_TIMEOUT_SECONDS from ralph.timeout_defaults"
    )


@pytest.mark.parametrize("module_name", SDK_BACKEND_MODULES)
def test_sdk_backends_route_through_with_timeout(module_name: str) -> None:
    source = _source_for(module_name)
    assert "from ralph.mcp.websearch._bounded_sdk_call import" in source, (
        f"{module_name} must import from ralph.mcp.websearch._bounded_sdk_call"
    )
    assert "with_timeout(" in source, (
        f"{module_name} must call with_timeout(...) to bound the SDK call"
    )
    tree = _tree_for(module_name)
    with_timeout_ancestors: set[int] = set()

    def _visit(node: ast.AST, inside_with_timeout: bool) -> None:
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "with_timeout":
                for child in ast.iter_child_nodes(node):
                    _visit(child, True)
                return
            if inside_with_timeout:
                with_timeout_ancestors.add(id(node))
        for child in ast.iter_child_nodes(node):
            _visit(child, inside_with_timeout)

    _visit(tree, False)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr == "search":
            assert id(node) in with_timeout_ancestors, (
                f"{module_name}:{node.lineno}: .search( call is not wrapped in with_timeout("
            )
    text_method_nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "text"
    ]
    for node in text_method_nodes:
        assert id(node) in with_timeout_ancestors, (
            f"{module_name}:{node.lineno}: .text( call is not wrapped in with_timeout("
        )


@pytest.mark.parametrize("module_name", SDK_BACKEND_MODULES)
def test_sdk_backends_do_not_directly_import_sdk_timeout_constant(module_name: str) -> None:
    source = _source_for(module_name)
    forbidden = "from ralph.timeout_defaults import WEBSEARCH_SDK_TIMEOUT_SECONDS"
    assert forbidden not in source, (
        f"{module_name} must NOT import {forbidden!r} directly; "
        f"import the re-exported constant or the "
        f"default_sdk_timeout_seconds() function from "
        f"ralph.mcp.websearch._bounded_sdk_call"
    )
