from __future__ import annotations

import ast
import re
from functools import cache
from pathlib import Path

_ALLOWLIST: dict[str, str] = {}

_BARE_MARKUP_RE = re.compile(
    r"\[(?:red|green|blue|yellow|cyan|magenta|bold|dim|italic|underline|white|black|orange)\]"
)

_SECOND_ARG_IDX = 1
_MIN_ARGS_FOR_POSITIONAL_STYLE = 2

_REPO_ROOT = Path(__file__).parent.parent.parent


@cache
def _target_files() -> tuple[Path, ...]:
    display_dir = _REPO_ROOT / "ralph-workflow" / "ralph" / "display"
    files = list(display_dir.glob("*.py"))
    files.append(_REPO_ROOT / "ralph-workflow" / "ralph" / "banner.py")
    files.append(_REPO_ROOT / "ralph-workflow" / "ralph" / "cli" / "main.py")
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


class _ViolationVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.style_violations: list[tuple[int, str]] = []
        self.append_violations: list[tuple[int, str]] = []
        self.markup_violations: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        for keyword in node.keywords:
            if keyword.arg != "style":
                continue
            val = keyword.value
            if isinstance(val, ast.Constant) and isinstance(val.value, str):
                literal = val.value
                if not literal.startswith("theme.") and literal not in _ALLOWLIST:
                    self.style_violations.append((val.lineno, literal))

        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "append":
            if len(node.args) >= _MIN_ARGS_FOR_POSITIONAL_STYLE:
                second_arg = node.args[_SECOND_ARG_IDX]
                if isinstance(second_arg, ast.Constant) and isinstance(second_arg.value, str):
                    literal = second_arg.value
                    if not literal.startswith("theme.") and literal not in _ALLOWLIST:
                        self.append_violations.append((second_arg.lineno, literal))
        elif isinstance(func, ast.Attribute) and func.attr == "print" and node.args:
            first_arg = node.args[0]
            if (
                isinstance(first_arg, ast.Constant)
                and isinstance(first_arg.value, str)
                and _BARE_MARKUP_RE.search(first_arg.value)
            ):
                self.markup_violations.append((first_arg.lineno, first_arg.value[:100]))


def _file_violations(
    source_path: Path,
) -> tuple[
    list[tuple[int, str]],
    list[tuple[int, str]],
    list[tuple[int, str]],
]:
    source = _file_source(source_path)
    if "style=" not in source and ".append(" not in source and "print(" not in source:
        return [], [], []

    tree = _parsed_tree(source_path)
    if tree is None:
        return [], [], []

    visitor = _ViolationVisitor()
    visitor.visit(tree)
    return visitor.style_violations, visitor.append_violations, visitor.markup_violations


_target_files()
for _f in _target_files():
    _parsed_tree(_f)


def test_display_style_contracts() -> None:
    style_violations: list[str] = []
    append_violations: list[str] = []
    markup_violations: list[str] = []

    for path in _target_files():
        rel = path.relative_to(_REPO_ROOT / "ralph-workflow")
        (
            file_style_violations,
            file_append_violations,
            file_markup_violations,
        ) = _file_violations(path)
        for lineno, literal in file_style_violations:
            style_violations.append(f"  {rel}:{lineno}: style={literal!r}")
        for lineno, literal in file_append_violations:
            append_violations.append(f"  {rel}:{lineno}: .append(..., {literal!r})")
        for lineno, literal in file_markup_violations:
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
