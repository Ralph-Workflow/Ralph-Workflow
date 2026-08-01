"""S-1 guards against bypassing Ralph's syntax palette selector."""

from __future__ import annotations

import ast
from pathlib import Path

_THEME_ARGUMENTS = {"theme", "code_theme", "inline_code_theme", "background_color"}
_CANDIDATE_TOKENS = (*_THEME_ARGUMENTS, "Markdown")


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    return call.func.attr if isinstance(call.func, ast.Attribute) else None


def _violations(tree: ast.AST) -> list[str]:
    """Return literal syntax-theme arguments, including Markdown's positional theme."""
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if (
            (_called_name(node) or "").endswith("Markdown")
            and len(node.args) > 1
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            violations.append(f"{node.lineno} code_theme")
        violations.extend(
            f"{node.lineno} {keyword.arg}"
            for keyword in node.keywords
            if keyword.arg in _THEME_ARGUMENTS
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
        )
    return violations


def test_s1_display_code_uses_no_literal_theme_or_background() -> None:
    """AC-C2/C4: display call sites must use named, background-derived palette values."""
    display = Path(__file__).parents[2] / "ralph" / "display"
    violations = [
        f"{path.relative_to(display)}:{violation}"
        for path in display.glob("*.py")
        if path.name != "theme.py"
        for source in [path.read_text(encoding="utf-8")]
        if any(token in source for token in _CANDIDATE_TOKENS)
        for violation in _violations(ast.parse(source, filename=str(path)))
    ]
    assert not violations, f"literal themes/backgrounds bypass the selector: {violations}"


def test_s1_guard_regression_rejects_positional_and_inline_markdown_themes() -> None:
    """DA-002: Markdown theme defaults cannot bypass the selector by argument form."""
    tree = ast.parse("""
Markdown(text, "ansi_dark")
Markdown(text, inline_code_theme="monokai")
AdaptiveMarkdown(text, "ansi_light")
""")
    assert _violations(tree) == ["2 code_theme", "3 inline_code_theme", "4 code_theme"]
