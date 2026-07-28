"""S-1 guards against bypassing Ralph's syntax palette selector."""

from __future__ import annotations

from pathlib import Path


def test_s1_display_code_does_not_select_rich_named_code_themes() -> None:
    display = Path(__file__).parents[2] / "ralph" / "display"
    forbidden = ('code_theme="ansi_', "code_theme='ansi_", 'theme="ansi_', "theme='ansi_")
    violations = [
        path.relative_to(display).as_posix()
        for path in display.glob("*.py")
        if any(value in path.read_text(encoding="utf-8") for value in forbidden)
    ]
    assert not violations, f"named Rich code themes bypass the selector: {violations}"
