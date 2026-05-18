"""AST-based policy tests enforcing file-size, single-class, and import rules.

Scoped to ralph/ and tests/ only (excludes .venv, tmp/, docs/).
"""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RALPH_DIR = REPO_ROOT / "ralph"
TESTS_DIR = REPO_ROOT / "tests"

_SKIP_DIRS = frozenset({"__pycache__", ".venv", "tmp"})


def _all_py_files(base: Path) -> list[Path]:
    result = []
    for path in base.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        result.append(path)
    return sorted(result)


def _count_lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def _top_level_classes(path: Path) -> list[str]:
    """Return names of top-level classes at module level.

    TYPE_CHECKING-guarded classes are in ast.If body nodes, not tree.body,
    so they are naturally excluded by iterating tree.body directly.
    """
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    return [node.name for node in tree.body if isinstance(node, ast.ClassDef)]


def _private_ralph_imports(path: Path) -> list[tuple[str, list[str]]]:
    """Return (module, [private_names]) for imports of private names from ralph.*."""
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []

    results = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if not mod.startswith("ralph"):
                continue
            private_names = [
                alias.name
                for alias in node.names
                if alias.name.startswith("_") and not alias.name.startswith("__")
            ]
            if private_names:
                results.append((mod, private_names))
    return results


def _has_bypass_comment(path: Path) -> list[tuple[int, str]]:
    """Return (lineno, line) for lines with actual # type: ignore or # noqa comments.

    Uses tokenize to distinguish comment tokens from string literals.
    """
    src = path.read_text(encoding="utf-8")
    results = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(src).readline)
        for tok_type, tok_string, (start_row, _), _, _ in tokens:
            if tok_type == tokenize.COMMENT and (
                "# type: ignore" in tok_string or "# noqa" in tok_string
            ):
                lines = src.splitlines()
                line = lines[start_row - 1] if start_row <= len(lines) else ""
                results.append((start_row, line.rstrip()))
    except tokenize.TokenError:
        pass
    return results


def test_no_file_over_1000_lines() -> None:
    """No .py file in ralph/ or tests/ may exceed 1000 lines."""
    violations = []
    for base in (RALPH_DIR, TESTS_DIR):
        for path in _all_py_files(base):
            n = _count_lines(path)
            if n > 1000:
                rel = str(path.relative_to(REPO_ROOT))
                violations.append(f"{n} lines: {rel}")

    assert not violations, (
        f"Files exceeding 1000 lines ({len(violations)} violations):\n"
        + "\n".join(sorted(violations))
    )


def test_one_class_per_file() -> None:
    """Each .py file in ralph/ or tests/ must have at most one top-level class."""
    violations = []
    for base in (RALPH_DIR, TESTS_DIR):
        for path in _all_py_files(base):
            classes = _top_level_classes(path)
            if len(classes) > 1:
                rel = str(path.relative_to(REPO_ROOT))
                violations.append(f"{len(classes)} classes in {rel}: {classes[:5]}")

    assert not violations, (
        f"Files with multiple top-level classes ({len(violations)} violations):\n"
        + "\n".join(sorted(violations))
    )


def test_no_private_imports_from_ralph_in_tests() -> None:
    """Test files must not import private symbols (starting with _) from ralph.* modules."""
    violations = []
    for path in _all_py_files(TESTS_DIR):
        for mod, names in _private_ralph_imports(path):
            rel = str(path.relative_to(REPO_ROOT))
            violations.append(f"{rel}: from {mod} import {names}")

    assert not violations, (
        f"Private ralph.* imports in tests ({len(violations)} violations):\n"
        + "\n".join(sorted(violations))
    )


def test_no_type_ignore_or_noqa_in_maintained_source() -> None:
    """No # type: ignore or # noqa bypass comments in ralph/ or tests/."""
    violations = []
    for base in (RALPH_DIR, TESTS_DIR):
        for path in _all_py_files(base):
            hits = _has_bypass_comment(path)
            if hits:
                rel = str(path.relative_to(REPO_ROOT))
                for lineno, line in hits:
                    violations.append(f"{rel}:{lineno}: {line}")

    assert not violations, (
        f"Bypass comments found ({len(violations)} violations):\n"
        + "\n".join(sorted(violations))
    )
