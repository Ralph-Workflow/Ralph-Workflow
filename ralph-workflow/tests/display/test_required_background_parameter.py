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
            return index < len(node.args.posonlyargs) + len(node.args.args) - len(
                node.args.defaults
            )
        return (
            node.args.kw_defaults[index - len(node.args.posonlyargs) - len(node.args.args)] is None
        )
    return False


def _has_resolved_background(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Accept an explicit background flag or a required DisplayContext carrier."""
    if _has_required_background(node):
        return True
    return any(
        argument.arg in {"ctx", "display_context"}
        and any(
            isinstance(child, ast.Name) and child.id == "DisplayContext"
            for child in ast.walk(argument.annotation)
        )
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        if argument.annotation is not None
    )


def _class_has_resolved_background(node: ast.ClassDef) -> bool:
    """Accept a constructor with a required background flag or context carrier."""
    initializer = next(
        (
            child
            for child in node.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == "__init__"
        ),
        None,
    )
    if initializer is None or _has_required_background(initializer):
        return initializer is not None
    args = (*initializer.args.posonlyargs, *initializer.args.args)
    required_count = len(args) - len(initializer.args.defaults)
    return any(
        argument.arg == "display_context"
        and index < required_count
        and argument.annotation is not None
        and any(
            isinstance(child, ast.Name) and child.id == "DisplayContext"
            for child in ast.walk(argument.annotation)
        )
        for index, argument in enumerate(args)
    )


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
    violations = [
        f"{node.lineno} {node.name}" for node in guarded_classes if not _class_has_resolved_background(node)
    ]
    violations.extend(
        f"{node.lineno} {node.name}"
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _requires_background(node)
        and not _has_resolved_background(node)
    )
    return sorted(violations)


_DISPLAY = Path(__file__).parents[2] / "ralph" / "display"
_BACKGROUND_PARAMETER_VIOLATIONS = [
    f"{path.relative_to(_DISPLAY)}:{violation}"
    for path in _DISPLAY.glob("*.py")
    for violation in _violations(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
]


def test_s1_color_builders_require_resolved_background() -> None:
    """AC-C7: AST discovery catches future colour paths without a hardcoded list."""
    assert not _BACKGROUND_PARAMETER_VIOLATIONS, (
        f"colour builders need required terminal_bg_is_light: {_BACKGROUND_PARAMETER_VIOLATIONS}"
    )


def test_s1_guard_regression_rejects_defaulted_background_on_renderable_constructor() -> None:
    """DA-001: a CodeBlock wrapper cannot hide a defaulted background in its renderer."""
    tree = ast.parse("""
class SneakyBlock(CodeBlock):
    def __init__(self, terminal_bg_is_light=None):
        self.terminal_bg_is_light = terminal_bg_is_light

    def __rich_console__(self):
        return syntax_theme_for_background(self.terminal_bg_is_light)

class SneakyMarkdown(Markdown):
    def __init__(self, terminal_bg_is_light=None):
        self.terminal_bg_is_light = terminal_bg_is_light
""")
    assert _violations(tree) == ["2 SneakyBlock", "9 SneakyMarkdown"]


def test_s1_guard_accepts_display_context_constructor() -> None:
    """DA-001: constructors may receive the resolved DisplayContext carrier."""
    tree = ast.parse("""
class ContextualBlock(CodeBlock):
    def __init__(self, display_context: DisplayContext):
        self.display_context = display_context

    def __rich_console__(self):
        return syntax_theme_for_background(True)
""")
    assert _violations(tree) == []
