"""S-1 prevents any display colour builder from silently choosing a palette."""

from __future__ import annotations

import ast
from pathlib import Path

_TARGETS = {"Syntax", "Markdown", "CodeBlock", "syntax_theme_for_background", "pick_status_styles"}


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    return call.func.attr if isinstance(call.func, ast.Attribute) else None


def _requires_background(node: ast.AST) -> bool:
    return any(
        isinstance(call, ast.Call) and _called_name(call) in _TARGETS for call in ast.walk(node)
    )


def _has_required_background(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    args = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
    for index, argument in enumerate(args):
        if argument.arg != "terminal_bg_is_light":
            continue
        positional = index < len(node.args.posonlyargs) + len(node.args.args)
        if positional:
            return index < len(node.args.posonlyargs) + len(node.args.args) - len(node.args.defaults)
        return node.args.kw_defaults[index - len(node.args.posonlyargs) - len(node.args.args)] is None
    return False


def _violations(tree: ast.AST) -> list[str]:
    """Return colour builders that do not require their resolved background."""
    guarded_classes = {
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and (
            _requires_background(node)
            or any(
                isinstance(base, ast.Name) and base.id.endswith(("CodeBlock", "Markdown"))
                for base in node.bases
            )
        )
    }
    violations: list[str] = []
    for node in guarded_classes:
        initializer = next(
            (
                child
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == "__init__"
            ),
            None,
        )
        if initializer is None or not _has_required_background(initializer):
            violations.append(f"{node.lineno} {node.name}")
    violations.extend(
        f"{node.lineno} {node.name}"
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _requires_background(node)
        and not _has_required_background(node)
    )
    return sorted(violations)


def test_s1_color_builders_require_resolved_background() -> None:
    """AC-C7: AST discovery catches future colour paths without a hardcoded list."""
    display = Path(__file__).parents[2] / "ralph" / "display"
    violations = [
        f"{path.relative_to(display)}:{violation}"
        for path in display.glob("*.py")
        for violation in _violations(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    ]
    assert not violations, f"colour builders need required terminal_bg_is_light: {violations}"


def test_s1_guard_regression_rejects_defaulted_background_on_renderable_constructor() -> None:
    """DA-001: a CodeBlock wrapper cannot hide a defaulted background in its renderer."""
    tree = ast.parse('''
class SneakyBlock(CodeBlock):
    def __init__(self, terminal_bg_is_light=None):
        self.terminal_bg_is_light = terminal_bg_is_light

    def __rich_console__(self):
        return syntax_theme_for_background(self.terminal_bg_is_light)

class SneakyMarkdown(Markdown):
    def __init__(self, terminal_bg_is_light=None):
        self.terminal_bg_is_light = terminal_bg_is_light
''')
    assert _violations(tree) == ["2 SneakyBlock", "9 SneakyMarkdown"]
