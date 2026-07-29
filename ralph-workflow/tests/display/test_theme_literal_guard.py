"""S-1 guards against bypassing Ralph's syntax palette selector."""

from __future__ import annotations

import ast
from pathlib import Path


def test_s1_display_code_uses_no_literal_theme_or_background() -> None:
    """AC-C2/C4: display call sites must use named, background-derived palette values."""
    display = Path(__file__).parents[2] / "ralph" / "display"
    violations: list[str] = []
    for path in display.glob("*.py"):
        if path.name == "theme.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations.extend(
            f"{path.relative_to(display)}:{node.lineno} {keyword.arg}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for keyword in node.keywords
            if keyword.arg in {"theme", "code_theme", "background_color"}
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
        )
    assert not violations, f"literal themes/backgrounds bypass the selector: {violations}"
