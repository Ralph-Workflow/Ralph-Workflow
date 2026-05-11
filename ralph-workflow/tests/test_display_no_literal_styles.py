"""Regression test: no literal Rich style strings in production display code.

Walks ralph/display/, ralph/banner.py, and ralph/cli/main.py using ast.parse.
Checks three categories of forbidden literals:

  1. ``style=`` keyword argument: value must be a theme key ('theme.*') or allowlisted.
  2. ``.append(text, style_literal)`` positional second arg: must be a theme key or allowlisted.
  3. ``console.print("[bare_color]text")`` markup strings: first positional arg must not contain
     bare Rich colour/style tags like [red], [green], [bold], etc.

The allowlist is intentionally narrow. Add an entry only when the style cannot
be expressed as a theme key (e.g., a runtime-computed value passed through a
helper function parameter).
"""

from __future__ import annotations

import ast
import re
from functools import cache
from pathlib import Path

# Documented exceptions: style strings that are allowed as plain literals.
# Each entry maps literal_value -> reason string (for documentation).
_ALLOWLIST: dict[str, str] = {
    # No permanent allowlist entries at this time.
    # Add here only with a clear justification comment.
}

# Bare Rich colour/style tags that must not appear in markup strings passed to print()
_BARE_MARKUP_RE = re.compile(
    r"\[(?:red|green|blue|yellow|cyan|magenta|bold|dim|italic|underline|white|black|orange)\]"
)

# Index for second positional arg in a call
_SECOND_ARG_IDX = 1
_MIN_ARGS_FOR_POSITIONAL_STYLE = 2

_REPO_ROOT = Path(__file__).parent.parent.parent


@cache
def _target_files() -> tuple[Path, ...]:
    display_dir = _REPO_ROOT / "ralph-workflow" / "ralph" / "display"
    cli_dir = _REPO_ROOT / "ralph-workflow" / "ralph" / "cli"
    cli_commands_dir = cli_dir / "commands"
    extras = (
        _REPO_ROOT / "ralph-workflow" / "ralph" / "banner.py",
        _REPO_ROOT / "ralph-workflow" / "ralph" / "config" / "welcome.py",
    )
    files = (
        list(display_dir.glob("*.py"))
        + list(cli_dir.glob("*.py"))
        + list(cli_commands_dir.glob("*.py"))
        + list(extras)
    )
    return tuple(f for f in files if f.is_file())


@cache
def _file_source(source_path: Path) -> str:
    return source_path.read_text(encoding="utf-8")


@cache
def _parsed_tree(source_path: Path) -> ast.AST | None:
    source = _file_source(source_path)
    try:
        return ast.parse(source, filename=str(source_path))
    except SyntaxError:
        return None


def _literal_style_violations(source_path: Path) -> list[tuple[int, str]]:
    """Return (line_number, literal_value) for each forbidden bare literal style= arg."""
    if "style=" not in _file_source(source_path):
        return []
    tree = _parsed_tree(source_path)
    if tree is None:
        return []

    violations: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "style":
                continue
            val = keyword.value
            # Bare string constant — must be theme key or allowlisted
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                literal = val.value
                if literal.startswith("theme."):
                    continue
                if literal in _ALLOWLIST:
                    continue
                violations.append((val.lineno, literal))
    return violations


def _positional_append_style_violations(source_path: Path) -> list[tuple[int, str]]:
    """Return (line, value) for .append(text, style_literal) positional bare style args.

    Catches Text.append("text", "bare_style") where the second positional arg is a string
    constant that is not a theme key.
    """
    if ".append(" not in _file_source(source_path):
        return []
    tree = _parsed_tree(source_path)
    if tree is None:
        return []

    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "append"):
            continue
        # Second positional arg is a bare style literal
        if len(node.args) >= _MIN_ARGS_FOR_POSITIONAL_STYLE:
            second_arg = node.args[_SECOND_ARG_IDX]
            if isinstance(second_arg, ast.Constant) and isinstance(second_arg.value, str):
                literal = second_arg.value
                if not literal.startswith("theme.") and literal not in _ALLOWLIST:
                    violations.append((second_arg.lineno, literal))
    return violations


def _markup_string_violations(source_path: Path) -> list[tuple[int, str]]:
    """Return (line, value) for console.print() calls containing bare markup color tags.

    Catches console.print("[red]text[/red]") style calls where a bare colour string is
    embedded as Rich markup in the first positional argument.
    """
    source = _file_source(source_path)
    if "print(" not in source or "[" not in source:
        return []
    tree = _parsed_tree(source_path)
    if tree is None:
        return []

    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "print"):
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if (
            isinstance(first_arg, ast.Constant)
            and isinstance(first_arg.value, str)
            and _BARE_MARKUP_RE.search(first_arg.value)
        ):
            violations.append((first_arg.lineno, first_arg.value[:100]))
    return violations


# Pre-populate caches at module import time so the I/O and AST parsing
# happen before the per-test SIGALRM window is set up.
_target_files()
for _f in _target_files():
    _parsed_tree(_f)


def test_display_style_contracts() -> None:
    """Display code must avoid bare style literals and bare markup colors."""
    style_violations: list[str] = []
    append_violations: list[str] = []
    markup_violations: list[str] = []

    for path in _target_files():
        rel = path.relative_to(_REPO_ROOT / "ralph-workflow")
        for lineno, literal in _literal_style_violations(path):
            style_violations.append(f"  {rel}:{lineno}: style={literal!r}")
        for lineno, literal in _positional_append_style_violations(path):
            append_violations.append(f"  {rel}:{lineno}: .append(..., {literal!r})")
        for lineno, literal in _markup_string_violations(path):
            markup_violations.append(f"  {rel}:{lineno}: print({literal!r})")

    assert not style_violations, (
        "Bare literal Rich style= strings found"
        " — replace with 'theme.*' keys or add to allowlist:\n" + "\n".join(style_violations)
    )
    assert not append_violations, (
        "Bare literal style as second positional arg to .append() found"
        " — use style= keyword with a 'theme.*' key or add to allowlist:\n"
        + "\n".join(append_violations)
    )
    assert not markup_violations, (
        "Bare markup color tags in console.print() strings found"
        " — use Text objects with 'theme.*' keys instead:\n" + "\n".join(markup_violations)
    )
