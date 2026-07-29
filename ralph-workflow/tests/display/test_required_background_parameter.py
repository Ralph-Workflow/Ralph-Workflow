"""S-1 prevents any display colour builder from silently choosing a palette."""

from __future__ import annotations

import ast
from pathlib import Path

_TARGETS = {"Syntax", "Markdown", "CodeBlock", "syntax_theme_for_background", "pick_status_styles"}


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    return call.func.attr if isinstance(call.func, ast.Attribute) else None


def _requires_background(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(call, ast.Call) and _called_name(call) in _TARGETS
        for call in ast.walk(node)
    )


def _has_required_background(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    args = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
    for index, argument in enumerate(args):
        if argument.arg != "terminal_bg_is_light":
            continue
        positional = index < len(node.args.posonlyargs) + len(node.args.args)
        if positional:
            defaults = node.args.defaults
            return index < len(node.args.posonlyargs) + len(node.args.args) - len(defaults)
        return node.args.kw_defaults[index - len(node.args.posonlyargs) - len(node.args.args)] is None
    return False


def test_s1_color_builders_require_resolved_background() -> None:
    """AC-C7: AST discovery catches future colour paths without a hardcoded list."""
    display = Path(__file__).parents[2] / "ralph" / "display"
    violations: list[str] = []
    for path in display.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _requires_background(node):
                if node.name == "__rich_console__":
                    continue  # uses the required value captured by its enclosing renderable constructor
                if not _has_required_background(node):
                    violations.append(f"{path.relative_to(display)}:{node.lineno} {node.name}")
    assert not violations, f"colour builders need required terminal_bg_is_light: {violations}"
