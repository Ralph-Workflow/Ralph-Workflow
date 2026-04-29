"""AST-based test: ensure no hardcoded color literals in display style= arguments.

This test scans ralph/display/, ralph/banner.py, and ralph/cli/main.py for
`style=` keyword arguments whose values are raw color strings (e.g. "cyan",
"bold green") rather than semantic theme keys ("theme.*") or pure modifier
combinations ("bold", "dim", "italic").

Allowed:
  - style="theme.*"          (semantic theme key)
  - style="bold"             (pure Rich modifier)
  - style="dim"              (pure Rich modifier)
  - style="italic"           (pure Rich modifier)
  - style="bold dim"         (modifier combination)
  - style=<variable>         (not a string literal)
  - style=<f-string>         (Okabe-Ito hex colors via constants)

Forbidden:
  - style="cyan"
  - style="bold green"
  - style="dim italic"       (OK modifier combination, so actually allowed)
"""

from __future__ import annotations

import ast
import pathlib
from typing import NamedTuple

_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent.parent
_RALPH_ROOT = _REPO_ROOT / "ralph"

_TARGET_PATHS = [
    _RALPH_ROOT / "display",
    _RALPH_ROOT / "banner.py",
    _RALPH_ROOT / "cli" / "main.py",
]

_PURE_MODIFIERS = frozenset(
    {
        "bold",
        "dim",
        "italic",
        "underline",
        "blink",
        "strike",
        "reverse",
        "hidden",
        "not",
        "link",
    }
)


class Violation(NamedTuple):
    path: str
    line: int
    value: str


def _is_allowed_style_literal(value: str) -> bool:
    """Return True if the style literal is allowed."""
    if value.startswith("theme."):
        return True
    # Allow pure modifier combinations (no color names, no hex)
    words = set(value.lower().split())
    return words.issubset(_PURE_MODIFIERS)


class _StyleLiteralVisitor(ast.NodeVisitor):
    def __init__(self, filepath: str) -> None:
        self.filepath = filepath
        self.violations: list[Violation] = []

    def visit_Call(self, node: ast.Call) -> None:
        for keyword in node.keywords:
            if keyword.arg == "style" and isinstance(keyword.value, ast.Constant):
                value = keyword.value.value
                if isinstance(value, str) and not _is_allowed_style_literal(value):
                    self.violations.append(
                        Violation(
                            path=self.filepath,
                            line=keyword.value.lineno,
                            value=value,
                        )
                    )
        self.generic_visit(node)


def _collect_py_files(path: pathlib.Path) -> list[pathlib.Path]:
    if path.is_file():
        return [path] if path.suffix == ".py" else []
    return list(path.rglob("*.py"))


def test_no_literal_color_styles_in_display_modules() -> None:
    """All style= keyword arguments in display modules must use theme keys or pure modifiers."""
    all_violations: list[Violation] = []

    for target in _TARGET_PATHS:
        for py_file in _collect_py_files(target):
            source = py_file.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source, filename=str(py_file))
            except SyntaxError:
                continue
            visitor = _StyleLiteralVisitor(str(py_file.relative_to(_REPO_ROOT)))
            visitor.visit(tree)
            all_violations.extend(visitor.violations)

    if all_violations:
        lines = [
            f"  {v.path}:{v.line}: style={v.value!r} (use a 'theme.*' key instead)"
            for v in all_violations
        ]
        raise AssertionError(
            "Hardcoded color style literals found:\n" + "\n".join(lines)
        )
